import base64
from collections import Counter

from playwright.async_api import async_playwright

from app.models.schemas import (
    AnalysisResult,
    ColorPalette,
    ImageAsset,
    NavItem,
    SectionInfo,
    Typography,
)

# JS run inside the page to pull structured signals out of the live DOM.
# Doing this in-browser (vs. parsing raw HTML) gives us computed styles,
# which is what actually renders — not just what's written in CSS files.
EXTRACTION_SCRIPT = """
() => {
    function textSnippet(el) {
        const t = el.innerText || "";
        return t.trim().slice(0, 160) || null;
    }

    // Nav links
    const navEls = document.querySelectorAll("nav a, header a");
    const nav_items = Array.from(navEls).slice(0, 15).map(a => ({
        label: (a.innerText || "").trim().slice(0, 40),
        href: a.getAttribute("href")
    })).filter(n => n.label);

    // Top-level structural sections
    const sectionEls = document.querySelectorAll(
        "header, nav, main section, main > div, footer, section"
    );

    const sections = Array.from(sectionEls).slice(0, 25).map(el => ({
        tag: el.tagName.toLowerCase(),
        heading: (el.querySelector("h1, h2, h3") || {}).innerText || null,
        text_snippet: textSnippet(el),
        image_count: el.querySelectorAll("img").length
    }));

    // Images
    const imgEls = document.querySelectorAll("img");

    const images = Array.from(imgEls).slice(0, 20).map(img => ({
        src: img.src,
        alt: img.alt || null
    })).filter(i => i.src);

    // Colors + fonts via computed style sampling
    const sampleEls = document.querySelectorAll(
        "body, header, nav, main, section, h1, h2, p, a, button, footer"
    );

    const bgCounts = {};
    const textCounts = {};
    const fontCounts = {};

    let headingSize = null;
    let bodySize = null;

    sampleEls.forEach(el => {
        const cs = window.getComputedStyle(el);

        if (cs.backgroundColor !== "rgba(0, 0, 0, 0)") {
            bgCounts[cs.backgroundColor] =
                (bgCounts[cs.backgroundColor] || 0) + 1;
        }

        textCounts[cs.color] =
            (textCounts[cs.color] || 0) + 1;

        if (cs.fontFamily) {
            fontCounts[cs.fontFamily] =
                (fontCounts[cs.fontFamily] || 0) + 1;
        }

        if (el.tagName === "H1" && !headingSize) {
            headingSize = cs.fontSize;
        }

        if (el.tagName === "P" && !bodySize) {
            bodySize = cs.fontSize;
        }
    });

    const bodyCS = window.getComputedStyle(document.body);
    const htmlCS = window.getComputedStyle(document.documentElement);

    const pageBackground =
        bodyCS.backgroundColor !== "rgba(0, 0, 0, 0)"
            ? bodyCS.backgroundColor
            : htmlCS.backgroundColor !== "rgba(0, 0, 0, 0)"
                ? htmlCS.backgroundColor
                : "rgb(255, 255, 255)";

    const pageText = bodyCS.color;

    // Pruned DOM
    const SKIP = [
        "script",
        "style",
        "noscript",
        "svg",
        "iframe",
        "link",
        "meta",
        "template"
    ];

    function simplify(el, depth) {
        const tag = el.tagName.toLowerCase();

        if (SKIP.includes(tag) || depth > 12) {
            return "";
        }

        const cs = window.getComputedStyle(el);

        if (
            cs.display === "none" ||
            cs.visibility === "hidden"
        ) {
            return "";
        }

        const s = [];

        if (cs.backgroundColor !== "rgba(0, 0, 0, 0)") {
            s.push("bg:" + cs.backgroundColor);
        }

        if (cs.backgroundImage !== "none") {
            s.push(
                "bgimg:" +
                cs.backgroundImage
                    .slice(0, 120)
                    .replace(/"/g, "'")
            );
        }

        s.push("color:" + cs.color);
        s.push("fs:" + cs.fontSize);

        if (cs.fontWeight !== "400") {
            s.push("fw:" + cs.fontWeight);
        }

        if (cs.display !== "block") {
            s.push("d:" + cs.display);
        }

        if (cs.display.includes("flex")) {
            s.push(
                "flex:" +
                cs.flexDirection +
                "/" +
                cs.justifyContent +
                "/" +
                cs.alignItems +
                "/gap " +
                cs.gap
            );
        }

        if (cs.padding !== "0px") {
            s.push("p:" + cs.padding);
        }

        if (cs.textAlign !== "start") {
            s.push("ta:" + cs.textAlign);
        }

        if (cs.borderRadius !== "0px") {
            s.push("br:" + cs.borderRadius);
        }

        let attrs = ' data-s="' + s.join(";") + '"';

        if (el.getAttribute("href")) {
            attrs +=
                ' href="' +
                el.getAttribute("href") +
                '"';
        }

        if (tag === "img") {
            attrs +=
                ' src="' +
                (el.currentSrc || el.src) +
                '"';

            if (el.alt) {
                attrs +=
                    ' alt="' +
                    el.alt +
                    '"';
            }
        }

        let inner = "";

        el.childNodes.forEach(n => {
            if (n.nodeType === 3) {
                const t = n.textContent.trim();

                if (t) {
                    inner += t.slice(0, 300);
                }
            } else if (n.nodeType === 1) {
                inner += simplify(n, depth + 1);
            }
        });

        // Drop empty wrappers, but keep images and decorative blocks
        if (
            !inner &&
            !["img", "input", "hr", "br"].includes(tag) &&
            cs.backgroundImage === "none"
        ) {
            return "";
        }

        return (
            "<" +
            tag +
            attrs +
            ">" +
            inner +
            "</" +
            tag +
            ">"
        );
    }

    const simplified_html =
        simplify(document.body, 0).slice(0, 40000);

    // Hacker News stories
    // Extract them directly from the browser DOM instead of
    // asking the LLM to discover/copy them from HTML.
    const rows = Array.from(
        document.querySelectorAll("tr.athing")
    ).slice(0, 30);

    const stories = rows
        .map(r => {
            const sub = r.nextElementSibling;
            const a = r.querySelector(".titleline > a");

            return {
                rank:
                    (r.querySelector(".rank") || {}).innerText || "",

                title:
                    a ? a.innerText : "",

                url:
                    a ? a.href : "",

                site:
                    (r.querySelector(".sitestr") || {}).innerText || "",

                points:
                    sub
                        ? (
                            (sub.querySelector(".score") || {})
                                .innerText || ""
                        )
                        : "",

                author:
                    sub
                        ? (
                            (sub.querySelector(".hnuser") || {})
                                .innerText || ""
                        )
                        : "",

                age:
                    sub
                        ? (
                            (sub.querySelector(".age") || {})
                                .innerText || ""
                        )
                        : "",

                comments:
                    sub
                        ? (
                            Array.from(
                                sub.querySelectorAll("a")
                            ).pop() || {}
                        ).innerText || ""
                        : "",

                id: r.id
            };
        })
        .filter(s => s.title);

    return {
        simplified_html,
        title: document.title,
        nav_items,
        sections,
        images,
        stories,

        bgColorCounts: bgCounts,
        textColorCounts: textCounts,
        fontCounts,

        headingSize,
        bodySize,
        pageBackground,
        pageText
    };
}
"""


async def analyze_website(url: str) -> AnalysisResult:
    """Load the given URL in a headless browser and extract layout/style signals."""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 900
            }
        )

        await page.goto(
            url,
            wait_until="networkidle",
            timeout=30000
        )

        await page.evaluate("""async () => {
            for (
                let y = 0;
                y < document.body.scrollHeight;
                y += 600
            ) {
                window.scrollTo(0, y);

                await new Promise(
                    r => setTimeout(r, 120)
                );
            }

            window.scrollTo(0, 0);
        }""")

        # IMPORTANT:
        # The JS function above is EXTRACTION_SCRIPT.
        extracted = await page.evaluate(EXTRACTION_SCRIPT)

        screenshot_bytes = await page.screenshot(
            full_page=True,
            type="png"
        )

        await browser.close()

    screenshot_b64 = base64.b64encode(
        screenshot_bytes
    ).decode("utf-8")

    # Rank colors by frequency
    bg_counts = Counter(
        extracted.get("bgColorCounts", {})
    )

    text_counts = Counter(
        extracted.get("textColorCounts", {})
    )

    top_bg = [
        c for c, _
        in bg_counts.most_common(4)
    ]

    top_text = [
        c for c, _
        in text_counts.most_common(4)
    ]

    font_counts = Counter(
        extracted.get("fontCounts", {})
    )

    top_fonts = [
        f for f, _
        in font_counts.most_common(3)
    ]

    page_bg = (
        extracted.get("pageBackground")
        or (top_bg[0] if top_bg else None)
    )

    page_text = (
        extracted.get("pageText")
        or (top_text[0] if top_text else None)
    )

    colors = ColorPalette(
        primary=top_bg[0] if top_bg else None,
        secondary=(
            top_bg[1]
            if len(top_bg) > 1
            else None
        ),
        background=page_bg,
        text=page_text,
        all_colors=list(
            dict.fromkeys(
                top_bg + top_text
            )
        ),
    )

    typography = Typography(
        font_families=top_fonts,
        heading_font_size=extracted.get(
            "headingSize"
        ),
        body_font_size=extracted.get(
            "bodySize"
        ),
    )

    nav_items = [
        NavItem(**n)
        for n in extracted.get(
            "nav_items",
            []
        )
    ]

    sections = [
        SectionInfo(**s)
        for s in extracted.get(
            "sections",
            []
        )
    ]

    images = [
        ImageAsset(**i)
        for i in extracted.get(
            "images",
            []
        )
    ]

    return AnalysisResult(
        url=url,
        title=extracted.get("title"),
        screenshot_base64=screenshot_b64,

        nav_items=nav_items,
        sections=sections,
        images=images,

        # Structured Hacker News stories
        stories=extracted.get(
            "stories",
            []
        ),

        colors=colors,
        typography=typography,

        simplified_html=extracted.get(
            "simplified_html"
        ),

        raw_dom_summary=(
            f"{len(sections)} sections, "
            f"{len(images)} images, "
            f"{len(nav_items)} nav links, "
            f"{len(extracted.get('stories', []))} stories detected"
        ),
    )
 