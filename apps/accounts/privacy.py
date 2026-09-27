"""Public privacy-policy helpers.

The Founder controls one platform-wide policy. Public rendering uses a cached,
sanitised HTML snapshot so the policy does not add repeated database work to
marketing/storefront traffic.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
import zipfile

from django.core.cache import cache


PRIVACY_POLICY_CACHE_KEY = "platform:privacy-policy:v1"
PRIVACY_POLICY_CACHE_SECONDS = 3600
PRIVACY_UPLOAD_EXTENSIONS = {".html", ".htm", ".txt", ".md", ".markdown", ".pdf", ".docx"}
PRIVACY_UPLOAD_MAX_BYTES = 10 * 1024 * 1024

DEFAULT_PRIVACY_POLICY_HTML = """
<p>INPROFIC, an ITERATID LTD product, recognises that privacy matters to the businesses that use our platform, their staff, customers, delivery participants, website visitors and other people whose information may be processed through our services. This Privacy Policy explains the information we handle, why we handle it, how it may be shared, and the choices available to you.</p>

<h2>Who we are</h2>
<p>INPROFIC is a business operations platform that connects inventory, procurement, production, sales, point-of-sale, commerce, payments, finance, delivery, reporting, notifications and related workflows. Depending on the context, ITERATID LTD may act as a data controller for platform account, billing, security and service-administration information, and as a data processor or service provider when a business uses INPROFIC to process information about its own staff, customers, suppliers or operations.</p>

<h2>Scope</h2>
<p>This policy applies to the INPROFIC marketing website, hosted application, tenant workspaces, hosted storefronts, checkout and order-tracking pages, delivery experiences, installed web application features, platform communications and headless API services. A business using INPROFIC may have its own privacy obligations and policy for information it controls. Third-party websites, payment providers, delivery providers and other services linked to or integrated with INPROFIC may also apply their own privacy terms.</p>

<h2>Information we collect</h2>
<h3>Information you provide</h3>
<ul>
<li>Account and workspace information, such as name, username, business name, email address, phone number, role and service type.</li>
<li>Business operational information entered into the platform, including products, inventory, recipes or formulas, procurement, production, sales, expenses, finance, customer orders, delivery records, reports and audit information.</li>
<li>Commerce and customer information needed to place, fulfil and support orders, such as customer name, contact details, delivery address, order contents, fulfilment choices and delivery instructions.</li>
<li>Payment-related information, such as transaction references, payment status, account-selection details and transfer proof uploaded for verification. Where a third-party payment provider handles payment credentials, those credentials are handled under that provider's systems and terms rather than being stored by INPROFIC unless expressly stated.</li>
<li>Delivery information, including assignment and route details, estimated arrival times, delivery status, customer-rider messages and delivery reports created while a delivery is active.</li>
<li>Files you choose to upload, such as storefront branding, payment evidence, business backup files or other documents used by an enabled feature.</li>
<li>Information you send when contacting support or otherwise communicating with us.</li>
</ul>
<h3>Information collected automatically</h3>
<ul>
<li>Basic device and request information such as IP address, browser or device type, operating environment, request timestamps and security-related request metadata.</li>
<li>Session and authentication information needed to keep signed-in users connected to the correct workspace and to protect requests against misuse.</li>
<li>First-party product analytics and operational events used to understand registrations, logins, subscription milestones and feature usage. INPROFIC is designed not to capture passwords, payment secrets or arbitrary form contents in product-analytics events.</li>
<li>Web-push subscription information when a user expressly enables device notifications.</li>
</ul>

<h2>How we use information</h2>
<p>We use information as reasonably necessary to:</p>
<ul>
<li>create and administer accounts, workspaces, subscriptions, roles and access controls;</li>
<li>provide inventory, procurement, production, sales, commerce, finance, delivery, reporting, API and related platform functions;</li>
<li>process and reconcile orders and payment states, including staff verification where a payment method requires it;</li>
<li>coordinate delivery assignments, route-aware estimates, live status updates and delivery communications;</li>
<li>send transactional, security, operational and service notifications;</li>
<li>provide customer support, diagnose faults, maintain service reliability and improve performance;</li>
<li>protect the platform, tenants and users against fraud, abuse, unauthorised access and other security threats;</li>
<li>measure first-party product usage and improve INPROFIC's services; and</li>
<li>comply with applicable legal obligations and enforce our agreements.</li>
</ul>

<h2>Legal bases for processing</h2>
<p>Where the Nigeria Data Protection Act 2023 or another law requiring a legal basis applies, processing may rely on consent, performance of a contract or steps requested before a contract, compliance with a legal obligation, protection of vital interests, public-interest grounds where applicable, or legitimate interests that do not override the rights and interests of the data subject. The applicable basis depends on the specific processing activity. A tenant business remains responsible for identifying an appropriate basis where it controls the information it places in INPROFIC.</p>

<h2>Cookies and browser storage</h2>
<p>INPROFIC uses a limited set of first-party cookies and browser-storage features that support core product functions. These may include:</p>
<ul>
<li><strong>Session cookies</strong> used to keep authenticated users signed in, maintain secure customer/order continuity and remember the active business workspace.</li>
<li><strong>CSRF security cookies</strong> used to protect browser requests and form submissions against cross-site request forgery.</li>
<li><strong>Local or session storage</strong> used for functional preferences such as storefront baskets, interface state, onboarding state, notification-tray preferences and temporary checkout or tracking continuity.</li>
</ul>
<p>These technologies are used for security and functionality rather than third-party behavioural advertising. INPROFIC does not load advertising or cross-site tracking cookies by default. If non-essential analytics or advertising technologies are introduced later, they should be subject to any consent or preference controls required by applicable law before they are activated.</p>
<p>You can clear or block cookies and browser storage using your browser controls, but disabling essential storage may prevent sign-in, checkout, saved basket, security or other core features from working correctly.</p>

<h2>How information is shared</h2>
<p>We may share or make information available only where reasonably necessary for the service, including with:</p>
<ul>
<li>authorised users of the relevant business workspace according to configured roles and permissions;</li>
<li>payment providers selected by the business or customer for payment processing and confirmation;</li>
<li>delivery providers, in-house riders and authorised dispatch users for delivery fulfilment;</li>
<li>hosting, storage, email, web-push and other infrastructure providers that help operate INPROFIC;</li>
<li>professional advisers, regulators, courts or public authorities where disclosure is required or permitted by law; and</li>
<li>a successor or relevant party in connection with a lawful business restructuring, financing, acquisition or transfer, subject to appropriate protections.</li>
</ul>
<p>We do not sell personal information for monetary consideration.</p>

<h2>Headless API and external websites</h2>
<p>Businesses may use the INPROFIC headless API to present catalogue, checkout, receipt, order and other commerce functions inside their own websites or applications. The operator of that external website remains responsible for its own privacy notice, cookies and customer-facing collection practices. Information sent to INPROFIC through the API is handled under this policy and the applicable service relationship.</p>

<h2>Data retention</h2>
<p>We retain information for as long as reasonably necessary to provide the service, maintain operational and accounting records, meet contractual or legal requirements, resolve disputes, protect security and enforce agreements. Retention periods can differ by record type and tenant configuration. Where a workspace or record is deleted, some information may remain for a limited period in backups, security records or records that must be retained by law before being deleted or anonymised in the ordinary course.</p>

<h2>Data security</h2>
<p>INPROFIC uses administrative and technical safeguards appropriate to the service, including role-based access controls, tenant scoping, secure transport in production, CSRF protections, controlled secrets, audit-oriented records and other measures intended to protect confidentiality, integrity and availability. No internet transmission or storage system can be guaranteed to be completely secure.</p>

<h2>Your privacy rights</h2>
<p>Depending on applicable law and our role in relation to the information, you may have rights to request access to personal data, correction of inaccurate data, deletion or erasure in applicable circumstances, restriction or objection to certain processing, withdrawal of consent where consent is the basis, data portability where applicable, and information about the processing of your data. You may also have the right to lodge a complaint with the Nigeria Data Protection Commission or another competent supervisory authority.</p>
<p>If your information is controlled by a business using INPROFIC, we may direct your request to that business or assist it in responding, as appropriate.</p>

<h2>International data transfers</h2>
<p>INPROFIC may use infrastructure or service providers that process information in countries other than the country where it was originally collected. Where applicable law requires safeguards for an international transfer, the relevant controller will take reasonable steps to use an appropriate transfer mechanism or other lawful safeguard.</p>

<h2>Children's privacy</h2>
<p>INPROFIC is a business operations service and is not intended for children to create platform accounts on their own. Businesses using the platform should not submit children's personal information unless it is lawful, necessary for the relevant service and handled with any consent or safeguards required by applicable law.</p>

<h2>Third-party services and links</h2>
<p>INPROFIC may connect to third-party payment, delivery, communications or other services, or link to external websites. Those third parties control their own services and privacy practices. We encourage users and businesses to review the terms and privacy information of third parties they choose to use.</p>

<h2>Marketing choices</h2>
<p>Where we send optional promotional communications, recipients may opt out using the unsubscribe method provided in the communication or by contacting us. Opting out of marketing does not prevent necessary service, security, billing or transactional messages.</p>

<h2>Changes to this policy</h2>
<p>We may update this Privacy Policy when INPROFIC's services, legal requirements or data practices change. The public page will show the effective or updated date of the current policy. Where a change is material and additional notice is appropriate or legally required, we may provide notice through the platform or another suitable channel.</p>

<h2>Contact us</h2>
<p>Questions, privacy requests or concerns about this policy may be sent through the INPROFIC support contact shown on this page. Where a request relates to data controlled by an INPROFIC business customer, please identify the relevant business so the request can be routed appropriately.</p>
""".strip()

_ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "h2", "h3", "ul", "ol", "li"}
_VOID_TAGS = {"br"}


class _PolicyHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._suppressed = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button"}:
            self._suppressed += 1
            return
        if self._suppressed or tag not in _ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        if not self._suppressed and tag.lower() == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button"}:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if not self._suppressed and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppressed:
            self.out.append(escape(data))


def sanitize_privacy_html(value: str) -> str:
    parser = _PolicyHTMLSanitizer()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.out).strip()


def _plain_or_markdown_to_html(value: str) -> str:
    """Convert a deliberately small text/Markdown subset into safe HTML."""
    lines = (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    paragraph = []
    list_kind = None

    def flush_paragraph():
        nonlocal paragraph
        text = " ".join(part.strip() for part in paragraph if part.strip()).strip()
        if text:
            out.append(f"<p>{escape(text)}</p>")
        paragraph = []

    def close_list():
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_paragraph(); close_list(); continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph(); close_list()
            level = "h2" if len(heading.group(1)) <= 2 else "h3"
            out.append(f"<{level}>{escape(heading.group(2).strip())}</{level}>")
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if bullet or ordered:
            flush_paragraph()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list(); out.append(f"<{kind}>"); list_kind = kind
            text = (bullet or ordered).group(1).strip()
            out.append(f"<li>{escape(text)}</li>")
            continue
        close_list()
        paragraph.append(line)
    flush_paragraph(); close_list()
    return "".join(out)


_DOCUMENT_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s",
    "h2", "h3", "h4", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td", "span",
}
_DOCUMENT_VOID_TAGS = {"br"}
_DOCUMENT_ALLOWED_CLASSES = {
    "doc-align-left", "doc-align-center", "doc-align-right", "doc-align-justify",
    "doc-table", "doc-empty",
}


class _RenderedDocumentSanitizer(HTMLParser):
    """Allow only the small markup vocabulary generated by our DOCX renderer."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._suppressed = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg"}:
            self._suppressed += 1
            return
        if self._suppressed or tag not in _DOCUMENT_ALLOWED_TAGS:
            return
        safe_attrs = []
        for key, value in attrs:
            key = (key or "").lower()
            value = value or ""
            if key == "class":
                classes = [token for token in value.split() if token in _DOCUMENT_ALLOWED_CLASSES]
                if classes:
                    safe_attrs.append(("class", " ".join(classes)))
            elif tag in {"td", "th"} and key in {"colspan", "rowspan"} and value.isdigit():
                safe_attrs.append((key, str(max(1, min(int(value), 50)))))
        rendered_attrs = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in safe_attrs)
        self.out.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag, attrs):
        if not self._suppressed and tag.lower() == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg"}:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if not self._suppressed and tag in _DOCUMENT_ALLOWED_TAGS and tag not in _DOCUMENT_VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppressed:
            self.out.append(escape(data))


def sanitize_rendered_document_html(value: str) -> str:
    parser = _RenderedDocumentSanitizer()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.out).strip()


def _docx_run_html(run) -> str:
    text = escape(run.text or "").replace("\n", "<br>")
    if not text:
        return ""
    if run.bold:
        text = f"<strong>{text}</strong>"
    if run.italic:
        text = f"<em>{text}</em>"
    if run.underline:
        text = f"<u>{text}</u>"
    if getattr(run.font, "strike", False):
        text = f"<s>{text}</s>"
    return text


def _docx_paragraph_html(paragraph, *, list_item=False) -> str:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    content = "".join(_docx_run_html(run) for run in paragraph.runs)
    if not content and not list_item:
        return '<p class="doc-empty"><br></p>'
    if list_item:
        return f"<li>{content}</li>"

    style_name = ((paragraph.style.name if paragraph.style else "") or "").strip().lower()
    if style_name in {"title", "subtitle"} or style_name.startswith("heading 1"):
        tag = "h2"
    elif style_name.startswith("heading 2"):
        tag = "h3"
    elif style_name.startswith("heading 3") or style_name.startswith("heading 4"):
        tag = "h4"
    else:
        tag = "p"

    alignment_map = {
        WD_ALIGN_PARAGRAPH.LEFT: "doc-align-left",
        WD_ALIGN_PARAGRAPH.CENTER: "doc-align-center",
        WD_ALIGN_PARAGRAPH.RIGHT: "doc-align-right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "doc-align-justify",
        WD_ALIGN_PARAGRAPH.DISTRIBUTE: "doc-align-justify",
    }
    class_name = alignment_map.get(paragraph.alignment)
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<{tag}{class_attr}>{content}</{tag}>"


def _render_docx_to_html(raw: bytes) -> str:
    """Render common Word structure without sending the document to a third party."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
    except ImportError as exc:
        raise ValueError("Word policy rendering requires the python-docx package installed with INPROFIC.") from exc

    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            if "word/document.xml" not in {item.filename for item in members}:
                raise ValueError("The Word file does not contain a readable document body.")
            # Protect the upload path from a highly compressed DOCX/ZIP bomb.
            if len(members) > 5000 or sum(item.file_size for item in members) > 50 * 1024 * 1024:
                raise ValueError("The Word policy document expands beyond the supported size.")
        document = Document(BytesIO(raw))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The Word file could not be opened. Upload a valid .docx document.") from exc

    parts = []
    open_list = None

    def close_list():
        nonlocal open_list
        if open_list:
            parts.append(f"</{open_list}>")
            open_list = None

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            style_name = ((paragraph.style.name if paragraph.style else "") or "").strip().lower()
            if "list bullet" in style_name:
                list_kind = "ul"
            elif "list number" in style_name:
                list_kind = "ol"
            else:
                list_kind = None
            if list_kind:
                if open_list != list_kind:
                    close_list()
                    parts.append(f"<{list_kind}>")
                    open_list = list_kind
                parts.append(_docx_paragraph_html(paragraph, list_item=True))
            else:
                close_list()
                parts.append(_docx_paragraph_html(paragraph))
        elif isinstance(child, CT_Tbl):
            close_list()
            table = Table(child, document)
            parts.append('<table class="doc-table"><tbody>')
            for row in table.rows:
                parts.append("<tr>")
                for cell in row.cells:
                    cell_html = []
                    for paragraph in cell.paragraphs:
                        fragment = "".join(_docx_run_html(run) for run in paragraph.runs)
                        cell_html.append(fragment or "&nbsp;")
                    parts.append(f"<td>{'<br>'.join(cell_html)}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table>")
    close_list()
    html = sanitize_rendered_document_html("".join(parts))
    if not re.sub(r"<[^>]+>", "", html).replace("&nbsp;", " ").strip():
        raise ValueError("The Word policy document does not contain readable text.")
    return html


def privacy_upload_to_content(upload) -> dict:
    """Validate and prepare a Founder-uploaded policy without flattening documents."""
    name = Path(getattr(upload, "name", "privacy-policy.txt")).name
    suffix = Path(name).suffix.lower()
    if suffix not in PRIVACY_UPLOAD_EXTENSIONS:
        raise ValueError("Upload HTML, HTM, TXT, Markdown, PDF or Word (.docx).")
    size = int(getattr(upload, "size", 0) or 0)
    if size > PRIVACY_UPLOAD_MAX_BYTES:
        raise ValueError("Keep the privacy-policy file below 10 MB.")
    raw = upload.read(PRIVACY_UPLOAD_MAX_BYTES + 1)
    if len(raw) > PRIVACY_UPLOAD_MAX_BYTES:
        raise ValueError("Keep the privacy-policy file below 10 MB.")
    if not raw:
        raise ValueError("The uploaded privacy-policy file is empty.")

    result = {
        "filename": name,
        "render_mode": "html",
        "body_html": None,
        "rendered_document_html": "",
    }
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("The uploaded PDF does not appear to be a valid PDF document.")
        result["render_mode"] = "pdf"
    elif suffix == ".docx":
        result["render_mode"] = "docx"
        result["rendered_document_html"] = _render_docx_to_html(raw)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Use a UTF-8 encoded HTML, TXT or Markdown policy file.") from exc
        if suffix in {".html", ".htm"}:
            html = sanitize_privacy_html(text)
        else:
            html = _plain_or_markdown_to_html(text)
        if not re.sub(r"<[^>]+>", "", html).strip():
            raise ValueError("The uploaded privacy-policy file is empty.")
        result["body_html"] = html

    try:
        upload.seek(0)
    except Exception:
        pass
    return result


def invalidate_privacy_policy_cache() -> None:
    cache.delete(PRIVACY_POLICY_CACHE_KEY)


def get_public_privacy_policy() -> dict:
    cached = cache.get(PRIVACY_POLICY_CACHE_KEY)
    if cached is not None:
        return cached
    from .models import PlatformPrivacyPolicy
    row = PlatformPrivacyPolicy.objects.filter(pk=1).values(
        "body_html", "rendered_document_html", "render_mode", "source_file",
        "effective_date", "updated_at"
    ).first()
    render_mode = (row or {}).get("render_mode") or PlatformPrivacyPolicy.RENDER_HTML
    source_file = (row or {}).get("source_file") or ""
    if render_mode in {PlatformPrivacyPolicy.RENDER_PDF, PlatformPrivacyPolicy.RENDER_DOCX} and not source_file:
        render_mode = PlatformPrivacyPolicy.RENDER_HTML

    raw_body = ((row or {}).get("body_html") or "").strip() or DEFAULT_PRIVACY_POLICY_HTML
    payload = {
        "render_mode": render_mode,
        # Sanitize at the public boundary even after Founder/admin validation.
        "body_html": sanitize_privacy_html(raw_body),
        "rendered_document_html": sanitize_rendered_document_html(
            (row or {}).get("rendered_document_html") or ""
        ),
        # Cache only the storage name. Generating a storage URL per request avoids
        # caching an expiring signed R2/S3 URL while still avoiding a DB query.
        "source_file": source_file,
        "effective_date": (row or {}).get("effective_date"),
        "updated_at": (row or {}).get("updated_at"),
    }
    cache.set(PRIVACY_POLICY_CACHE_KEY, payload, PRIVACY_POLICY_CACHE_SECONDS)
    return payload
