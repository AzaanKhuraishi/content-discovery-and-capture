"""Web discovery follows resource evidence, not filename-extension workflows."""
from __future__ import annotations
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import re

from ..domain import Observation, DiscoveryBatch, CaptureResult, CaptureError, AccessRequired, safe_url, safe_text
from ..runtimes.local import HttpProvider


class PageParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.title_parts = base, [], []
        self.dynamic = False
        self.password = False
        self.in_title = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        self.in_title = self.in_title or tag == "title"
        self.password |= tag == "input" and a.get("type") == "password"
        self.dynamic |= tag == "script" or a.get("aria-expanded") == "false" or "data-src" in a
        refs = []
        if tag in ("a", "link") and a.get("href"):
            refs.append((a["href"], "alternative" if a.get("rel") == "alternate" else "links-to"))
        if tag in ("img", "video", "audio", "source", "iframe", "embed", "object"):
            for key in ("src", "data-src", "data", "poster"):
                if a.get(key):
                    refs.append((a[key], "frame" if tag == "iframe" else "embeds"))
        for key in ("srcset", "data-srcset"):
            for entry in a.get(key, "").split(","):
                if entry.strip():
                    refs.append((entry.strip().split()[0], "embeds"))
        refs += [(match, "background") for match in re.findall(r'url\([\s\"\']*([^\)\"\']+)', a.get("style", ""))]
        for href, relation in refs:
            location = urljoin(self.base, href)
            if urlsplit(location).scheme not in ("http", "https"):
                continue
            item = {"url": location, "title": a.get("alt") or a.get("title") or "",
                    "relation": relation, "media_type": a.get("type", ""),
                    "native_id": a.get("data-resource-id") or a.get("data-content-id")}
            self.links.append(item)
            if tag == "a":
                self.anchor = item

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "a":
            self.anchor = None

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.anchor is not None:
            self.anchor["title"] += data


class WebAdapter:
    kind = "web"

    def __init__(self, http=None, browser=None):
        self.http = http or HttpProvider()
        self.browser = browser
        self.ephemeral = {}  # NEVER persisted, used only for authorised access.

    def inspect(self, url, scope, budget, asset=False):
        try:
            response = self.http.inspect(url, scope, budget.sample_bytes, asset)
        except (CaptureError, AccessRequired):
            if not self.browser:
                raise
            response = self.browser.inspect(url, scope, budget.sample_bytes, budget.max_actions)
        parser = PageParser(response.url)
        response_type = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "")
        if "html" in response_type:
            parser.feed(response.body.decode("utf-8", errors="replace"))
        if response.headers.get("X-CDC-Reused") == "true":
            previous = self.http.previous[url]
            parser.links = previous["metadata"]["navigation"]
            parser.title_parts = [previous["title"]]
        if parser.password:
            if not self.browser:
                raise AccessRequired("Sign in through an authorised browser, then resume this source.")
            response = self.browser.inspect(url, scope, budget.sample_bytes, budget.max_actions)
            parser = PageParser(response.url)
            parser.feed(response.body.decode("utf-8", errors="replace"))
            if parser.password:
                raise AccessRequired("Sign in through the browser, then resume this source.")
        elif parser.dynamic and self.browser:
            response = self.browser.inspect(url, scope, budget.sample_bytes, budget.max_actions)
            parser = PageParser(response.url)
            parser.feed(response.body.decode("utf-8", errors="replace"))
        return response, parser

    def discover(self, source, scope, cursor, budget):
        location = cursor["location"]
        asset = cursor.get("relation") in ("embeds", "background", "alternative", "frame")
        url = self.resolve({**cursor, "needs_resolution": cursor.get("needs_resolution", False)}, scope, budget)
        response, page = self.inspect(url, scope, budget, asset)
        headers = {k.lower(): v for k, v in response.headers.items()}
        media = headers.get("content-type", cursor.get("media_type") or "application/octet-stream").split(";")[0]
        title = " ".join(page.title_parts).strip() or cursor.get("title") or Path(urlsplit(location).path).name or location
        native = cursor.get("native_id")
        navigation = []
        for link in page.links:
            try:
                safe = safe_url(link["url"])
            except CaptureError:
                batch_reason = {"location": safe_text(link.get("url", "")),
                                "reason": "Invalid link omitted", "excluded": True}
                # The gap is added after the batch is created below.
                navigation.append({"url": "", "title": safe_text(link.get("title", "")),
                                   "relation": link.get("relation", "links-to"), "invalid": True,
                                   "gap": batch_reason})
                continue
            navigation.append({**link, "url": safe,
                               "needs_resolution": link.get("needs_resolution", False) or link["url"] != safe})
        observation = Observation(identity=native or location, location=location, title=title,
            kind="page" if "html" in media else "file", media_type=media,
            size=int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None,
            metadata={"etag": headers.get("etag"), "modified": headers.get("last-modified"),
                      "identity_basis": "native-id" if native else "stable-location",
                      "dynamic": page.dynamic, "discovery_method": response.method,
                      "navigation": [link for link in navigation if not link.get("invalid")],
                      "navigation_reused": headers.get("x-cdc-reused") == "true"},
            parent=cursor.get("parent"), relation=cursor.get("relation", "contains"),
            needs_resolution=cursor.get("needs_resolution", False))
        batch = DiscoveryBatch([observation], bytes_read=len(response.body), actions=response.actions)
        batch.gaps.extend(link["gap"] for link in navigation if link.get("invalid"))
        if not response.complete:
            batch.gaps.append({"location": location, "reason": "Inspection limit or unexplored dynamic structures"})
        if page.dynamic and not self.browser:
            batch.gaps.append({"location": location, "reason": "Dynamic content requires a browser capability"})
        for link in page.links:
            raw = link["url"]
            try:
                safe = safe_url(raw)
            except CaptureError:
                continue
            if not link.get("needs_resolution"):
                self.ephemeral[(safe, location)] = raw
            child_asset = link["relation"] in ("embeds", "background", "alternative", "frame")
            permitted = scope.permits(safe, "web", child_asset)
            info = {"location": safe, "title": safe_text(link["title"].strip()) or Path(urlsplit(safe).path).name,
                    "parent": location, "depth": cursor["depth"] + 1, "relation": link["relation"],
                    "native_id": link["native_id"], "media_type": link["media_type"], "needs_resolution": raw != safe or link.get("needs_resolution", False)}
            # Each reference is a placement; contents are inspected later when authorised by scope.
            batch.observations.append(Observation(identity=link["native_id"] or safe, location=safe,
                title=info["title"], parent=location, relation=link["relation"], external=not permitted,
                needs_resolution=info["needs_resolution"], metadata={"reference_only": True,
                "identity_basis": "native-id" if link["native_id"] else "stable-location"}))
            if permitted:
                if info["depth"] <= scope.max_depth:
                    batch.frontier.append(info)
                else:
                    batch.gaps.append({"location": safe, "reason": "Depth limit"})
        return batch

    def resolve(self, observation, scope, budget=None):
        location = observation["location"]
        if not observation.get("needs_resolution"):
            return location
        key = (location, observation.get("parent"))
        if key in self.ephemeral:
            return self.ephemeral[key]
        parent = observation.get("parent")
        if not parent or budget is None:
            raise AccessRequired("Revisit the stable parent to resolve this protected representation.")
        previous = getattr(self.http, "previous", None)
        if previous is not None:
            self.http.previous = {key: value for key, value in previous.items() if key != parent}
        try:
            response = self.http.inspect(parent, scope, budget.sample_bytes)
        finally:
            if previous is not None:
                self.http.previous = previous
        parser = PageParser(response.url)
        parser.feed(response.body.decode("utf-8", errors="replace"))
        matches = []
        for link in parser.links:
            try:
                if safe_url(link["url"]) == location:
                    matches.append(link["url"])
            except CaptureError:
                continue
        if len(set(matches)) != 1:
            raise AccessRequired("Protected representation needs unambiguous resolution through its source.")
        self.ephemeral[key] = matches[0]
        return matches[0]

    def methods(self, observation):
        if observation.get("external") or observation.get("metadata", {}).get("capture_unavailable"):
            return []
        result = ["http"]
        if self.browser:
            offered = self.browser.capabilities()
            if offered.get("download"):
                result.append("browser-download")
            if offered.get("snapshot") and observation.get("kind") == "page":
                result.append("browser-snapshot")
        return result

    def capture(self, observation, scope, method, destination, max_bytes, timeout):
        from ..domain import Budget
        location = self.resolve(observation, scope, Budget(sample_bytes=min(max_bytes, 256 * 1024)))
        asset = observation.get("relation") in ("embeds", "background", "alternative", "frame")
        if method == "http":
            if not scope.permits(location, "web", asset=asset):
                raise CaptureError("Representation is outside the approved source scope.")
            headers, final = self.http.download(location, scope, destination, max_bytes, timeout, True)
            lower = {k.lower(): v for k, v in headers.items()}
            old_etag = observation["metadata"].get("etag")
            if old_etag and lower.get("etag") and old_etag != lower["etag"]:
                raise CaptureError("Source version changed after review; refresh before capture.")
            return CaptureResult(destination, lower.get("content-type", observation["media_type"]).split(";")[0])
        if self.browser and method in self.methods(observation):
            return self.browser.capture(location, scope, destination, max_bytes, timeout, method)
        raise CaptureError("Requested browser method is not available in this runtime.")
