"""Actual pinned non-request contexts; residual scan, not JS semantics."""

import copy
from pathlib import Path
import unittest
import release_builder as builder
import test_release_tools as fixtures

# Independently captured from 53c1c556 / index-eWLpoAiz.js, SHA256
# 52dbf6a1720ca122a9e5b9b53891b6a5fe5a35e3753faf4bcc34b1115b285ff4.
CONTEXT_0 = (
    'var m="(?:(?:[a-z]+:)?//)",p="(?:\\\\S+(?::\\\\S*)?@)?",h=d.v4().source,g=d.v6'
    '().source,y="(?:(?:[a-z\\\\u00a1-\\\\uffff0-9][-_]*)*[a-z\\\\u00a1-\\\\uffff0-9]+)'
    '",v="(?:\\\\.(?:[a-z\\\\u00a1-\\\\uffff0-9]-*)*[a-z\\\\u00a1-\\\\uffff0-9]+)*",b="(?'
    ':\\\\.(?:[a-z\\\\u00a1-\\\\uffff]{2,}))",x="(?::\\\\d{2,5})?",C=\'(?:[/?#][^\\\\s"]*)'
    '?\',S="(?:".concat(m,"|www\\\\.)").concat(p,"(?:localhost|").concat(h,"|").co'
    'ncat(g,"|").concat(y).concat(v).concat(b,")").concat(x).concat(C);return Y'
    'p=new RegExp("(?:^".concat(S,"$)"),"i"),Yp}'
).encode("utf-8")

CONTEXT_1 = (
    'W$=typeof window<"u"&&typeof document<"u",RC=typeof navigator=="object"&&n'
    'avigator||void 0,$ve=W$&&(!RC||["ReactNative","NativeScript","NS"].indexOf'
    '(RC.product)<0),Eve=typeof WorkerGlobalScope<"u"&&self instanceof WorkerGl'
    'obalScope&&typeof self.importScripts=="function",_ve=W$&&window.location.h'
    'ref||"http://localhost",Ive=Object.freeze(Object.defineProperty({__proto__'
    ":null,hasBrowserEnv:W$,hasStandardBrowserEnv:$ve,hasStandardBrowserWebWork"
    'erEnv:Eve,navigator:RC,origin:_ve},Symbol.toStringTag,{value:"Module"})),Q'
    "r={...Ive,...wve};"
).encode("utf-8")

CONTEXT_2 = (
    '{key:"forwarded_allow_ips",label:a("FORWARDED_ALLOW_IPS"),children:e.forwa'
    'rded_allow_ips_set?f.jsx("span",{style:Gc,children:sL(e)}):"未设置（uvicorn 默认'
    '只信任 127.0.0.1）"}];return f.jsxs(f.Fragment,'
).encode("utf-8")

CONTEXTS = (CONTEXT_0, CONTEXT_1, CONTEXT_2)


class FrontendOriginContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = Path(__file__).resolve().parents[3]
        cls.lock = builder.git(
            repo, "cat-file", "blob", "6684bbce496513b7b2abb31392c84bf68b650f6f"
        )
        cls.front = {
            "index.html": b'<script src="/assets/app.js"></script>',
            "assets/app.js": b'const API="https://47.110.76.9";' + b";".join(CONTEXTS),
        }

    def reject(self, front, lock=None):
        with self.assertRaisesRegex(builder.Rejected, "^frontend_origin_mismatch$"):
            builder.validate_frontend_origin(front, self.lock if lock is None else lock)

    def test_actual_three_contexts_accepted(self):
        builder.validate_frontend_origin(self.front, self.lock)

    def test_plain_production_origin_needs_no_exception(self):
        builder.validate_frontend_origin({"index.html": b"https://47.110.76.9"}, b"{}")

    def test_wrong_source_lock_rejected(self):
        self.reject(self.front, self.lock + b" ")

    def test_context_variants_rejected(self):
        edits = [
            (b"return Yp=new RegExp", b"return fetch"),
            (b"window.location.href", b"window.location.origin"),
            ("默认只信任".encode(), "默认信任".encode()),
        ]
        for old, new in edits:
            with self.subTest(context=old):
                changed = copy.deepcopy(self.front)
                changed["assets/app.js"] = changed["assets/app.js"].replace(old, new)
                self.reject(changed)

    def test_duplicate_context_across_assets_rejected(self):
        for context in CONTEXTS:
            with self.subTest(context=context[:30]):
                self.reject(self.front | {"assets/duplicate.js": context})

    def test_fourth_hit_rejected(self):
        self.reject(self.front | {"assets/extra.js": b'const server="localhost";'})

    def test_bad_api_with_production_origin_still_rejected(self):
        for target in (
            b"http://localhost:8000",
            b"http://127.0.0.1:9000",
            b"https://localhost:54321/api",
            b"http://LOCALHOST:8000",
        ):
            with self.subTest(target=target):
                self.reject(
                    self.front
                    | {"assets/api.js": b'axios.create({baseURL:"' + target + b'"});'}
                )

    def test_request_inside_context_rejected(self):
        changed = copy.deepcopy(self.front)
        changed["assets/app.js"] = changed["assets/app.js"].replace(
            b"origin:_ve}", b'origin:fetch("http://localhost")}'
        )
        self.reject(changed)

    def test_missing_production_origin_rejected(self):
        changed = copy.deepcopy(self.front)
        changed["assets/app.js"] = changed["assets/app.js"].replace(
            b"https://47.110.76.9", b"https://other.invalid"
        )
        self.reject(changed)

    def test_trial_zeabur_rejected(self):
        for target in (b"http://trial-web", b"https://old.zeabur.app"):
            with self.subTest(target=target):
                self.reject(self.front | {"assets/api.js": target})

    def test_html_css_contexts_rejected(self):
        for suffix in ("html", "css"):
            with self.subTest(suffix=suffix):
                self.reject(
                    {
                        "index.html": b"https://47.110.76.9",
                        "assets/copy." + suffix: CONTEXT_1,
                    }
                )

    def test_full_builder_uses_git_lock_before_exception(self):
        fixture = fixtures.BuilderTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.put("frontend/package-lock.json", self.lock)
        fixture.refresh_source_pins()
        fixture.pins["frontend"]["package_lock_sha256"] = builder.digest(self.lock)
        raw = self.front["assets/app.js"]
        (fixture.dist / "assets/app.js").write_bytes(raw)
        fixture.pins["frontend"]["files"]["assets/app.js"] = {
            "bytes": len(raw),
            "sha256": builder.digest(raw),
        }
        self.assertFalse(fixture.build()["deployable"])
        fixture.put("frontend/package-lock.json", self.lock + b" ")
        fixture.refresh_source_pins()
        with self.assertRaisesRegex(builder.Rejected, "^frontend_lock_mismatch$"):
            fixture.build("second.tar.gz")


if __name__ == "__main__":
    unittest.main()
