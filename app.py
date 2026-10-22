"""
新聞クリッピング 配信選定アプリ v8
- 左：記事一覧だけ独立スクロール
- 右：プレビュー常時表示
- 下：出力テーブル
"""

import re
import os
import json
import tempfile
import tomllib
import unicodedata
import zipfile
from io import BytesIO
from numbers import Number
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ───────────────────────────────────────────────
# 優先度キーワード定義
# ───────────────────────────────────────────────
# キーワードの追加・修正は app.py ではなく keyword_config.toml を編集する
KEYWORD_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "keyword_config.toml")
DEFAULT_KEYWORD_CONFIG = {
    "primary_company_keywords": [
        "JX金属", "ＪＸ金属", "JX金属の銅建値",
        "日本鉱業", "日立鉱山", "久原房之助",
    ],
    "self_related_keywords": [
        "東邦チタニウム", "パンパシフィック・カッパー", "パンパシフィックカッパー", "PPC",
        "nikko metal", "ニッコー・メタル", "タツタ電線", "佐賀関",
        "カセロネス", "Caserones", "エスコンディーダ", "Escondida",
        "ロスペランブレス", "Los Pelambres", "TANIOBIS", "タニオビス",
        "タンタル", "銅箔", "ノベルクリスタルテクノロジー",
        "ロータス・サーマル・ソリューションズ", "スパッタリングターゲット",
        "ニオブ", "丸運", "高純度塩化物", "CVD・ALD材料",
        "モリブデン化合物", "インジウムリン", "光電融合",
        "電磁波シールド", "負熱膨張材", "3Dプリンター用金属粉", "JX", "ＪＸ",
    ],
    "competitor_keywords": [
        "住友金属鉱山", "三菱マテリアル", "三井金属鉱業", "三井金属",
        "DOWA", "古河機械金属", "日鉄鉱業", "小名浜製錬", "日比共同製錬",
        "信越化学工業", "信越化学", "東邦亜鉛", "マテリアル・コンセプト",
        "半導体材料", "半導体素材", "半導体の材料", "半導体の素材", "半導体",
        "レアアース", "生成AI", "生成ＡＩ",
    ],
    "industry_keywords": [
        "銅価", "銅建値", "銅地金", "銅電線", "日本鉱業協会", "伸銅協会",
        "重要鉱物", "クリティカル・メタル", "レアメタル", "LIBリサイクル",
        "リチウムイオン電池", "金属リサイクル", "電子ごみ", "データセンター",
        "メダル・プロジェクト銅価",
    ],
    "esg_keywords": [
        "CO2回収", "ブルーカーボン", "サーキュラーエコノミー",
        "ネイチャーポジティブ", "TNFD",
    ],
    "jx_keywords": ["JX金属", "ＪＸ金属", "JX METALS", "JXメタル"],
}
KEYWORD_CONFIG_ERROR: str | None = None


def load_keyword_config(path: str) -> dict[str, list[str]]:
    global KEYWORD_CONFIG_ERROR

    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        keyword_section = raw.get("keywords")
        if not isinstance(keyword_section, dict):
            raise ValueError("[keywords] セクションがありません。")

        loaded: dict[str, list[str]] = {}
        for key, default_values in DEFAULT_KEYWORD_CONFIG.items():
            values = keyword_section.get(key)
            if values is None:
                loaded[key] = list(default_values)
                continue
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"[keywords].{key} は文字列の配列で指定してください。")
            loaded[key] = values

        KEYWORD_CONFIG_ERROR = None
        return loaded
    except Exception as e:
        KEYWORD_CONFIG_ERROR = f"keyword_config.toml の読み込みに失敗したため、app.py 内の既定値を使用しています: {e}"
        return {key: list(values) for key, values in DEFAULT_KEYWORD_CONFIG.items()}


KEYWORD_CONFIG = load_keyword_config(KEYWORD_CONFIG_PATH)
PRIMARY_COMPANY_KEYWORDS = KEYWORD_CONFIG["primary_company_keywords"]
SELF_RELATED_KEYWORDS = KEYWORD_CONFIG["self_related_keywords"]
COMPETITOR_KEYWORDS = KEYWORD_CONFIG["competitor_keywords"]
INDUSTRY_KEYWORDS = KEYWORD_CONFIG["industry_keywords"]
ESG_KEYWORDS = KEYWORD_CONFIG["esg_keywords"]

PRIORITY_RANK = {
    "自社名・旧社名": 1, "自社関連": 2,
    "競合他社": 3, "業界関連": 4, "ESG関連": 5, "未分類": 6,
}

PRIORITY_COLOR = {
    "自社名・旧社名": "#1565c0", "自社関連": "#c62828",
    "競合他社": "#0b8043", "業界関連": "#6a1b9a",
    "ESG関連": "#546e7a", "未分類": "#9e9e9e",
}

PRIORITY_BADGE = {
    "自社名・旧社名": "🔵", "自社関連": "🔴",
    "競合他社": "🟢", "業界関連": "🟣", "ESG関連": "⚫", "未分類": "⚪",
}

HIGHLIGHT_ORDER = [
    ("自社名・旧社名", PRIMARY_COMPANY_KEYWORDS),
    ("自社関連", SELF_RELATED_KEYWORDS),
    ("競合他社", COMPETITOR_KEYWORDS),
    ("業界関連", INDUSTRY_KEYWORDS),
    ("ESG関連", ESG_KEYWORDS),
]

INTRA_NEWSPAPERS = {"日刊産業新聞", "鉄鋼新聞", "電波新聞"}
JX_KEYWORDS = KEYWORD_CONFIG["jx_keywords"]

NEWSPAPERS = [
    "日本経済新聞電子版", "日本経済新聞", "読売新聞", "西部読売新聞", "朝日新聞", "毎日新聞",
    "産経新聞", "東京新聞", "北海道新聞", "日刊工業新聞",
    "日刊産業新聞", "鉄鋼新聞", "化学工業日報", "電波新聞",
    "日本農業新聞", "茨城新聞", "大分合同新聞",
]
NEWSPAPERS_BY_LENGTH = sorted(NEWSPAPERS, key=len, reverse=True)
LOCAL_NEWSPAPERS = {"西部読売新聞", "北海道新聞", "東京新聞", "茨城新聞", "大分合同新聞"}
LOCAL_META_KEYWORDS = ["地方版", "地方経済面", "地域面", "西部本社", "大阪本社", "中部本社", "北海道支社", "九州"]
NIKKEI_DIGITAL_KEYWORDS = ["日本経済新聞電子版", "日経電子版", "NIKKEI電子版"]


@dataclass
class Article:
    title: str = ""
    date: str = "不明"
    newspaper: str = "不明"
    edition: str = "不明"
    page: str = "不明"
    charcount: str = ""
    has_pdf: bool = False
    body: str = ""
    priority: str = "未分類"
    local_flag: bool = False
    nikkei_digital_flag: bool = False


# ───────────────────────────────────────────────
# 分類
# ───────────────────────────────────────────────
def normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", s).upper().strip()


def contains_any(text: str, keywords: list[str]) -> bool:
    normalized = normalize(text)
    return any(normalize(keyword) in normalized for keyword in keywords)


def classify(title: str, body: str = "") -> str:
    text = f"{title} {body}"
    if contains_any(text, PRIMARY_COMPANY_KEYWORDS):
        return "自社名・旧社名"
    if contains_any(text, SELF_RELATED_KEYWORDS):
        return "自社関連"
    if contains_any(text, COMPETITOR_KEYWORDS):
        return "競合他社"
    if contains_any(text, INDUSTRY_KEYWORDS):
        return "業界関連"
    if contains_any(text, ESG_KEYWORDS):
        return "ESG関連"
    return "未分類"


def highlight_matches(text: str) -> str:
    matches: list[tuple[int, int, str]] = []
    for category, keywords in HIGHLIGHT_ORDER:
        for keyword in keywords:
            for match in re.finditer(re.escape(keyword), text, flags=re.IGNORECASE):
                matches.append((match.start(), match.end(), category))

    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    picked: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, category in matches:
        if start < last_end:
            continue
        picked.append((start, end, category))
        last_end = end

    if not picked:
        return escape(text)

    chunks: list[str] = []
    cursor = 0
    for start, end, category in picked:
        if cursor < start:
            chunks.append(escape(text[cursor:start]))
        color = PRIORITY_COLOR.get(category, "#9e9e9e")
        chunks.append(
            f'<span class="kw-highlight" style="background:{color};">'
            f"{escape(text[start:end])}</span>"
        )
        cursor = end
    if cursor < len(text):
        chunks.append(escape(text[cursor:]))
    return "".join(chunks)


def has_jx(title: str, body: str = "") -> bool:
    nc = (title + " " + body).upper()
    return any(kw.upper() in nc for kw in JX_KEYWORDS)


def has_primary_company_keyword(title: str, body: str = "") -> bool:
    return contains_any(f"{title} {body}", PRIMARY_COMPANY_KEYWORDS)


def sync_checkbox_state(indices: list[int]) -> None:
    selected = st.session_state.selected
    for i in indices:
        st.session_state[f"cb_{i}"] = i in selected


def ensure_checkbox_state(indices: list[int]) -> None:
    selected = st.session_state.selected
    for i in indices:
        key = f"cb_{i}"
        if key not in st.session_state:
            st.session_state[key] = i in selected


def handle_checkbox_change(index: int) -> None:
    checked = bool(st.session_state.get(f"cb_{index}", False))
    if checked:
        st.session_state.selected.add(index)
    else:
        st.session_state.selected.discard(index)


def format_article_picker_label(index: int, article: Article) -> str:
    badge = PRIORITY_BADGE.get(article.priority, "*")
    page_text = article.page if article.nikkei_digital_flag else f"{article.page}P"
    flags: list[str] = []
    if article.has_pdf:
        flags.append("PDF")
    if article.local_flag:
        flags.append("地方")
    if article.nikkei_digital_flag:
        flags.append("電子版")
    suffix = f" [{' / '.join(flags)}]" if flags else ""
    return f"{badge} {article.title} | {article.newspaper} | {article.date} | {page_text}{suffix}"


def render_keyboard_shortcuts(filtered_indices: list[int], preview_index: int | None) -> None:
    config = {
        "filteredIndices": filtered_indices,
        "previewIndex": preview_index,
    }
    components.html(
        f"""
<script>
const config = {json.dumps(config, ensure_ascii=True)};
const rootWindow = window.parent || window;
rootWindow.clipNavigatorConfig = config;

const hasClassPrefix = (node, prefix) => {{
  let current = node;
  while (current) {{
    const classes = typeof current.className === "string" ? current.className.split(/\\s+/) : [];
    if (classes.some((name) => name.startsWith(prefix))) {{
      return current;
    }}
    current = current.parentElement;
  }}
  return null;
}};

const isTypingTarget = (node) => {{
  const tagName = node?.tagName?.toLowerCase() || "";
  const inputType = node?.type?.toLowerCase() || "";
  return (
    tagName === "textarea" ||
    node?.isContentEditable ||
    (tagName === "input" && !["checkbox", "button", "submit"].includes(inputType))
  );
}};

const getPreviewIndex = (button) => {{
  const previewRoot = hasClassPrefix(button, "st-key-prev_");
  if (!previewRoot) {{
    return null;
  }}
  const classes = typeof previewRoot.className === "string" ? previewRoot.className.split(/\\s+/) : [];
  const hit = classes.find((name) => name.startsWith("st-key-prev_"));
  if (!hit) {{
    return null;
  }}
  const indexText = hit.replace("st-key-prev_", "");
  const index = Number.parseInt(indexText, 10);
  return Number.isNaN(index) ? null : index;
}};

const triggerPreview = (button) => {{
  const previewIndex = getPreviewIndex(button);
  const currentPreview = rootWindow.clipNavigatorConfig?.previewIndex;
  if (previewIndex === null || previewIndex === currentPreview) {{
    return;
  }}
  if (button.dataset.clipPreviewPending === "1") {{
    return;
  }}
  button.dataset.clipPreviewPending = "1";
  rootWindow.requestAnimationFrame(() => button.click());
}};

const bindPreviewInteractionHandlers = () => {{
  rootWindow.document.querySelectorAll('[class*="st-key-prev_"] button').forEach((button) => {{
    if (button.dataset.clipPreviewBound === "1") {{
      return;
    }}
    button.dataset.clipPreviewBound = "1";
    button.addEventListener("mouseenter", () => triggerPreview(button));
    button.addEventListener("focus", () => {{
      if (rootWindow.document.activeElement === button) {{
        triggerPreview(button);
      }}
    }});
  }});
}};

bindPreviewInteractionHandlers();
rootWindow.requestAnimationFrame(bindPreviewInteractionHandlers);
setTimeout(bindPreviewInteractionHandlers, 150);

if (!rootWindow.clipCheckboxEnterHandler) {{
  rootWindow.clipCheckboxEnterHandler = function(event) {{
    if (!["Enter", " "].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) {{
      return;
    }}

    const active = event.target;
    if (isTypingTarget(active)) {{
      return;
    }}

    const checkboxRoot = hasClassPrefix(active, "st-key-cb_");
    if (!checkboxRoot) {{
      return;
    }}

    const target =
      checkboxRoot.querySelector('[data-testid="stCheckbox"] label') ||
      checkboxRoot.querySelector("label") ||
      checkboxRoot.querySelector('[role="checkbox"]') ||
      checkboxRoot.querySelector('[data-baseweb="checkbox"]') ||
      checkboxRoot.querySelector('input[type="checkbox"]');

    if (!target) {{
      return;
    }}

    event.preventDefault();
    event.stopPropagation();
    rootWindow.requestAnimationFrame(() => target.click());
  }};
  rootWindow.document.addEventListener("keydown", rootWindow.clipCheckboxEnterHandler, true);
}}
</script>
""",
        height=0,
    )


def is_local_article(newspaper: str, edition: str, meta: str) -> bool:
    if newspaper in LOCAL_NEWSPAPERS:
        return True
    if "地方" in edition:
        return True
    return any(keyword in meta for keyword in LOCAL_META_KEYWORDS)


def is_nikkei_digital_article(newspaper: str, meta: str) -> bool:
    if newspaper == "日本経済新聞電子版":
        return True
    return any(keyword in meta for keyword in NIKKEI_DIGITAL_KEYWORDS)


# ───────────────────────────────────────────────
# パーサー
# ───────────────────────────────────────────────
META_RE = re.compile(r"\d{4}[/\.]\d{1,2}[/\.]\d{1,2}.+?(?:\d+\s*ページ|\d+\s*文字)")
DATE_RE = re.compile(r"(\d{4})[/\.](\d{1,2})[/\.](\d{1,2})")
PAGE_RE = re.compile(r"(\d{1,3})\s*ページ")
CHAR_RE = re.compile(r"(\d+)\s*文字")
PGSZ_RE = re.compile(r"^\s*\d+ページ\d+[KkMm][Bb]\s*$")
PRINT_MARK = "印刷対象にする"
SHOSHIN = "その他の書誌情報を表示"


def extract_meta(meta: str) -> dict:
    d = {
        "date": "不明", "newspaper": "不明", "edition": "不明",
        "page": "不明", "charcount": "", "has_pdf": False, "local_flag": False, "nikkei_digital_flag": False,
    }
    m = DATE_RE.search(meta)
    if m:
        d["date"] = f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
    for np in NEWSPAPERS_BY_LENGTH:
        if np in meta:
            d["newspaper"] = np
            break
    if "地方経済面" in meta:
        d["edition"] = "地方経済面"
    elif "夕刊" in meta:
        d["edition"] = "夕刊"
    else:
        d["edition"] = "朝刊"
    m = PAGE_RE.search(meta)
    if m:
        d["page"] = m.group(1)
    m = CHAR_RE.search(meta)
    if m:
        d["charcount"] = m.group(1)
    d["has_pdf"] = "PDF有" in meta
    d["local_flag"] = is_local_article(d["newspaper"], d["edition"], meta)
    d["nikkei_digital_flag"] = is_nikkei_digital_article(d["newspaper"], meta)
    if d["nikkei_digital_flag"]:
        d["page"] = "日経電子版"
    return d


def parse_text(raw: str) -> list[Article]:
    lines = raw.splitlines()
    return _parse_new(lines) if any(PRINT_MARK in l for l in lines) else _parse_old(lines)



def _parse_new(lines: list[str]) -> list[Article]:
    """
    「印刷対象にする」を記事終端として扱う。
    先頭記事が最初の「印刷対象にする」より前にあるケースも拾う。
    """
    blocks: list[list[str]] = []
    buf: list[str] = []
    seen_meta_in_buf = False

    for ln in lines:
        tr = ln.strip()
        is_meta = bool(META_RE.search(tr))

        if PRINT_MARK in ln:
            if any(x.strip() for x in buf):
                blocks.append(buf[:])
            buf = []
            seen_meta_in_buf = False
        else:
            # 「印刷対象にする」が無い区間で記事が連結されている場合の切り分け。
            # 2つ目以降のメタ行に遭遇したら、直前のタイトル行から新ブロックにする。
            if is_meta and seen_meta_in_buf:
                last_non_empty_idx = -1
                for idx in range(len(buf) - 1, -1, -1):
                    if buf[idx].strip():
                        last_non_empty_idx = idx
                        break
                if last_non_empty_idx >= 0:
                    last_non_empty = buf[last_non_empty_idx].strip()
                    if not META_RE.search(last_non_empty) and last_non_empty != SHOSHIN:
                        head = buf[:last_non_empty_idx]
                        tail = buf[last_non_empty_idx:]
                        if any(x.strip() for x in head):
                            blocks.append(head)
                        buf = tail
                        seen_meta_in_buf = any(META_RE.search(x.strip()) for x in buf)
                    else:
                        if any(x.strip() for x in buf):
                            blocks.append(buf[:])
                        buf = []
                        seen_meta_in_buf = False

            buf.append(ln)
            if is_meta:
                seen_meta_in_buf = True
    if any(x.strip() for x in buf):
        blocks.append(buf[:])

    arts: list[Article] = []
    for block in blocks:
        art = _parse_new_block(block)
        if art and (art.title or art.body or art.date != "不明"):
            arts.append(art)
    return arts



def _parse_new_block(lines: list[str]) -> Article | None:
    title = ""
    meta = ""
    body_lines: list[str] = []
    body_on = False

    for ln in lines:
        tr = ln.strip()
        if not tr:
            if body_on:
                body_lines.append(ln)
            continue

        if not title and tr != SHOSHIN and not META_RE.search(tr):
            title = tr
            continue

        if not meta and META_RE.search(ln):
            meta = tr
            continue

        if tr == SHOSHIN:
            body_on = True
            continue

        if body_on:
            if not body_lines and PGSZ_RE.match(tr):
                continue
            body_lines.append(ln)

    if not title and not meta and not body_lines:
        return None
    return _build(title, meta, "\n".join(body_lines))



def _parse_old(lines: list[str]) -> list[Article]:
    arts: list[Article] = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        if META_RE.search(ln):
            arts.append(_build("", ln, ""))
            i += 1
        elif i + 1 < len(lines) and META_RE.search(lines[i + 1]):
            arts.append(_build(ln, lines[i + 1], ""))
            i += 2
        else:
            arts.append(_build(ln, "", ""))
            i += 1
    return arts



def _build(title: str, meta: str, body: str) -> Article:
    a = Article(title=title.strip(), body=body.strip())
    d = extract_meta(meta)
    a.date = d["date"]
    a.newspaper = d["newspaper"]
    a.edition = d["edition"]
    a.page = d["page"]
    a.charcount = d["charcount"]
    a.has_pdf = d["has_pdf"]
    a.local_flag = d["local_flag"]
    a.nikkei_digital_flag = d["nikkei_digital_flag"]
    a.priority = classify(title, body)
    return a


# ───────────────────────────────────────────────
# 出力テーブル
# ───────────────────────────────────────────────
def date_sort_key(s: str):
    try:
        return datetime.strptime(s, "%Y/%m/%d")
    except Exception:
        return datetime.min



def build_priority_order_map(selected: list[Article]) -> dict[int, int]:
    ordered = sorted(
        list(enumerate(selected)),
        key=lambda x: (
            PRIORITY_RANK.get(x[1].priority, 99),
            -date_sort_key(x[1].date).timestamp() if x[1].date != "不明" else float("inf"),
            x[1].newspaper,
            x[1].title,
        ),
    )
    return {orig_idx: n for n, (orig_idx, _) in enumerate(ordered, 1)}



def make_chiasapo_df(selected: list[Article]) -> pd.DataFrame:
    priority_no = build_priority_order_map(selected)
    arts = sorted(
        list(enumerate(selected)),
        key=lambda x: (date_sort_key(x[1].date), x[1].newspaper),
        reverse=True,
    )
    return pd.DataFrame([
        {
            "日付": a.date,
            "タイトル": a.title,
            "新聞社名": a.newspaper,
            "朝刊/夕刊": a.edition,
            "掲載面": a.page,
            "並び替え順": priority_no[idx],
        }
        for idx, a in arts
    ])



def make_bucho_df(selected: list[Article]) -> pd.DataFrame:
    arts = sorted(
        selected,
        key=lambda a: (
            PRIORITY_RANK.get(a.priority, 99),
            -date_sort_key(a.date).timestamp() if a.date != "不明" else float("inf"),
            a.newspaper,
            a.title,
        ),
    )
    return pd.DataFrame([
        {
            "順番": i,
            "日付": a.date,
            "タイトル": a.title,
            "新聞社名": a.newspaper,
            "朝刊/夕刊": a.edition,
            "掲載面": a.page,
            "配信": "●",
            "ファイリング": "●" if has_jx(a.title, a.body) else "",
            "イントラ": "●" if (a.newspaper in INTRA_NEWSPAPERS and has_primary_company_keyword(a.title, a.body)) else "",
            "12階掲示": "●" if has_jx(a.title, a.body) else "",
        }
        for i, a in enumerate(arts, 1)
    ])


def to_excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes | None:
    # openpyxl に依存せず、最小構成の .xlsx を直接生成する
    def col_letter(n: int) -> str:
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def xml_text(v) -> str:
        if pd.isna(v):
            return ""
        return escape(str(v)).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "&#10;")

    def display_width(value) -> int:
        text = "" if pd.isna(value) else str(value)
        width = 0
        for ch in text:
            width += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
        return width

    # 文字列で保持しているが、Excel上では数値として扱いたい列
    NUMERIC_TEXT_COLUMNS = {"掲載面"}

    def cell_xml(ref: str, value, *, column_name: str = "", is_header: bool = False) -> str:
        """
        Excelセルを値の型に応じて出力する。

        - int / float などの数値型:
            → Excelの数値セルとして出力
        - 「掲載面」の "5" / "12" のような数字文字列:
            → Excelの数値セルとして出力
        - 日付、タイトル、新聞社名、「日経電子版」など:
            → 文字列セルとして出力
        """
        if pd.isna(value):
            return f'<c r="{ref}"/>'

        # ヘッダーは必ず文字列
        if not is_header:
            # bool は Number のサブクラスなので除外
            if isinstance(value, Number) and not isinstance(value, bool):
                return f'<c r="{ref}"><v>{value}</v></c>'

            # 掲載面は Article.page が str のため、数字だけなら数値セルに変換する
            if column_name in NUMERIC_TEXT_COLUMNS and isinstance(value, str):
                stripped = value.strip()

                if re.fullmatch(r"[+-]?\d+", stripped):
                    return f'<c r="{ref}"><v>{int(stripped)}</v></c>'

                if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)", stripped):
                    return f'<c r="{ref}"><v>{stripped}</v></c>'

        text = xml_text(value)
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    columns = list(df.columns)
    rows = [columns] + df.astype(object).values.tolist()

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for c_idx, val in enumerate(row, start=1):
            ref = f"{col_letter(c_idx)}{r_idx}"
            column_name = columns[c_idx - 1] if r_idx > 1 else ""
            cells.append(
                cell_xml(
                    ref,
                    val,
                    column_name=column_name,
                    is_header=(r_idx == 1),
                )
            )
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    col_defs: list[str] = []
    for c_idx, col in enumerate(df.columns, start=1):
        values = [col] + df[col].tolist()
        max_width = max(display_width(value) for value in values) if values else 10
        excel_width = min(max(max_width * 1.2, 10), 60)
        col_defs.append(
            f'<col min="{c_idx}" max="{c_idx}" width="{excel_width:.1f}" customWidth="1"/>'
        )

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<cols>{"".join(col_defs)}</cols>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def render_export_table(df: pd.DataFrame, *, height: int = 420) -> None:
    table_html = df.to_html(index=False, escape=True, classes="export-table")
    st.markdown(
        f'<div class="export-table-wrap" style="max-height:{height}px;">{table_html}</div>',
        unsafe_allow_html=True,
    )


def build_eml_bytes(
    *,
    subject: str,
    to_list: list[str],
    cc_list: list[str],
    body_text: str,
    body_html: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = "; ".join(to_list)
    if cc_list:
        msg["Cc"] = "; ".join(cc_list)
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    for filename, data, mime_type in attachments or []:
        maintype, subtype = mime_type.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def build_msg_bytes_via_outlook(
    *,
    subject: str,
    to_list: list[str],
    cc_list: list[str],
    body_text: str,
    body_html: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
 ) -> tuple[bytes | None, str | None]:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as e:
        return None, "pywin32 import failed: " + str(e)

    tmpdir = tempfile.mkdtemp(prefix="clip_msg_")
    try:
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # olMailItem
        mail.Subject = subject
        mail.To = "; ".join(to_list)
        mail.CC = "; ".join(cc_list)
        if body_html:
            mail.BodyFormat = 2  # olFormatHTML
            mail.HTMLBody = body_html
        else:
            mail.Body = body_text

        for filename, data, _ in attachments or []:
            attach_path = os.path.join(tmpdir, filename)
            with open(attach_path, "wb") as f:
                f.write(data)
            mail.Attachments.Add(attach_path)

        msg_path = os.path.join(tmpdir, "mail.msg")
        mail.SaveAs(msg_path, 3)  # olMSG
        with open(msg_path, "rb") as f:
            return f.read(), None
    except Exception as e:
        return None, "Outlook MSG save failed: " + str(e)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except Exception:
                pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass


# ───────────────────────────────────────────────
# UI
# ───────────────────────────────────────────────
st.set_page_config(page_title="クリッピング選定", page_icon="📰", layout="wide", initial_sidebar_state="expanded")

if KEYWORD_CONFIG_ERROR:
    st.warning(KEYWORD_CONFIG_ERROR)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap');
html, body, [class*="css"] {font-family:'Noto Sans JP', sans-serif;}
.stApp {background:#f0f2f5;}
[data-testid="stSidebar"]{background:#1a1f2e;border-right:2px solid #2d3550;}
[data-testid="stSidebar"] *{color:#c8d0e0 !important;}
[data-testid="stSidebar"] textarea{
    background:#0f1219 !important;border:1px solid #2d3550 !important;
    color:#8ec5fc !important;font-family:'IBM Plex Mono',monospace !important;font-size:11px !important;
}
.clip-header{background:linear-gradient(135deg,#1a1f2e 0%,#2d3550 100%);
    color:white;padding:16px 24px;border-radius:10px;margin-bottom:16px;
    display:flex;align-items:center;gap:12px;}
.clip-header h1{margin:0;font-size:22px;font-weight:700;}
.clip-header p{margin:0;font-size:12px;color:#8ec5fc;}
.stat-row{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;}
.stat-chip{background:white;border-radius:20px;padding:4px 14px;font-size:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;color:white;margin-right:4px;}
.preview-card{background:white;border-radius:10px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.10);min-height:100%;}
.preview-title{font-size:15px;font-weight:700;color:#1a1f2e;line-height:1.7;}
.preview-meta{background:#f5f7fa;border-radius:6px;padding:10px 12px;font-size:12px;color:#555;margin:10px 0;}
.preview-body{font-size:13px;line-height:2;color:#333;white-space:pre-wrap;}
.preview-empty{display:flex;align-items:center;justify-content:center;min-height:360px;color:#aaa;flex-direction:column;gap:12px;}
.kw-highlight{
    color:white;font-weight:700;padding:1px 4px;border-radius:4px;
    box-shadow:inset 0 -1px 0 rgba(255,255,255,.15);
}
div[data-testid="stButton"] > button{
    text-align:left !important;white-space:normal !important;height:auto !important;padding:8px 12px !important;
    font-size:12px !important;line-height:1.5 !important;border-radius:6px !important;background:white !important;
    border:1px solid #e0e0e0 !important;color:#1a1f2e !important;box-shadow:0 1px 3px rgba(0,0,0,.06) !important;
}
div[data-testid="stButton"] > button:hover{box-shadow:0 3px 10px rgba(0,0,0,.15) !important;border-color:#aaa !important;}
.st-key-important_only button{
    background:linear-gradient(135deg,#1565c0 0%,#1a73e8 100%) !important;
    color:white !important;border:none !important;font-weight:700 !important;
}
.st-key-important_only button:hover{
    border:none !important;
    box-shadow:0 4px 12px rgba(26,115,232,.35) !important;
}
.export-table-wrap{
    overflow:auto;
    border:1px solid #d7dce5;
    border-radius:10px;
    background:white;
    box-shadow:0 1px 3px rgba(0,0,0,.06);
}
.export-table{
    width:100%;
    border-collapse:collapse;
    table-layout:fixed;
    font-size:12px;
}
.export-table th{
    position:sticky;
    top:0;
    z-index:1;
    background:#f5f7fa;
    color:#1a1f2e;
    text-align:left;
    padding:10px 12px;
    border-bottom:1px solid #d7dce5;
}
.export-table td{
    padding:10px 12px;
    border-bottom:1px solid #eef1f5;
    vertical-align:top;
    white-space:normal;
    word-break:break-word;
    line-height:1.6;
}
.export-table th:nth-child(2),
.export-table td:nth-child(2){
    width:42%;
}
</style>
""",
    unsafe_allow_html=True,
)

for k, v in [("articles", []), ("selected", set()), ("preview", None)]:
    if k not in st.session_state:
        st.session_state[k] = v
if "pending_fil_prio" not in st.session_state:
    st.session_state.pending_fil_prio = None

with st.sidebar:
    st.markdown("### 📋 記事データを貼り付け")
    raw_input = st.text_area(
        "テレコンからコピーしたテキストをここへ",
        height=280,
        placeholder="「印刷対象にする」付き本文テキスト、または\nタイトル行＋メタデータ行のペアを貼り付け",
        key="raw_input",
    )
    c1, c2 = st.columns(2)
    with c1:
        parse_btn = st.button("🔍 解析", use_container_width=True, type="primary")
    with c2:
        clear_btn = st.button("🗑 クリア", use_container_width=True)

    if parse_btn and raw_input.strip():
        arts = parse_text(raw_input)
        st.session_state.articles = arts
        st.session_state.selected = set()
        st.session_state.preview = 0 if arts else None
        st.success(f"{len(arts)} 件の記事を解析しました")

    if clear_btn:
        st.session_state.articles = []
        st.session_state.selected = set()
        st.session_state.preview = None

    if st.session_state.articles:
        st.markdown("---")
        st.markdown("### 🔎 フィルタ")
        all_papers = sorted({a.newspaper for a in st.session_state.articles if a.newspaper != "不明"})
        st.multiselect("新聞名", all_papers, key="fil_paper")
        all_prio = sorted(PRIORITY_RANK, key=lambda p: PRIORITY_RANK[p])
        if st.session_state.pending_fil_prio is not None:
            st.session_state.fil_prio = st.session_state.pending_fil_prio
            st.session_state.pending_fil_prio = None
        st.multiselect("優先度", all_prio, key="fil_prio", default=[p for p in all_prio if p != "未分類"])
        st.text_input("キーワード検索", key="fil_kw", placeholder="タイトルを絞り込み")
        st.markdown("---")
        st.markdown(f"**選択中: {len(st.session_state.selected)} 件**")

articles = st.session_state.articles

st.markdown(
    """
<div class="clip-header">
  <span style="font-size:32px">📰</span>
  <div><h1>新聞クリッピング 配信選定</h1>
  <p>記事を解析 → 優先度で分類 → 配信リストを作成</p></div>
</div>
""",
    unsafe_allow_html=True,
)

if not articles:
    st.info("👈 左サイドバーに記事データを貼り付けて「解析」を押してください。")
    st.stop()


def apply_filter(arts: list[Article]) -> list[tuple[int, Article]]:
    # 元の配列位置を保持する。同一内容の記事が複数あっても widget key を重複させない。
    res = list(enumerate(arts))
    if st.session_state.get("fil_paper"):
        res = [(i, a) for i, a in res if a.newspaper in st.session_state["fil_paper"]]
    if st.session_state.get("fil_prio"):
        res = [(i, a) for i, a in res if a.priority in st.session_state["fil_prio"]]
    kw = st.session_state.get("fil_kw", "").strip()
    if kw:
        res = [(i, a) for i, a in res if kw.lower() in a.title.lower()]
    return res


filtered_pairs = apply_filter(articles)
filtered_idx = [i for i, _ in filtered_pairs]
filtered = [a for _, a in filtered_pairs]

if filtered_idx:
    if st.session_state.preview not in filtered_idx:
        st.session_state.preview = filtered_idx[0]
else:
    st.session_state.preview = None

render_keyboard_shortcuts(filtered_idx, st.session_state.preview)

prio_counts: dict[str, int] = {}
all_prio = sorted(PRIORITY_RANK, key=lambda p: PRIORITY_RANK[p])
for p in all_prio:
    prio_counts[p] = 0
for a in articles:
    if a.priority in prio_counts:
        prio_counts[a.priority] += 1
    else:
        prio_counts[a.priority] = prio_counts.get(a.priority, 0) + 1

chip_cols = st.columns(len(all_prio) + 2)
active_prio = st.session_state.get("fil_prio", [])
active_chip = active_prio[0] if len(active_prio) == 1 and active_prio[0] in all_prio else None
with chip_cols[0]:
    if st.button("🧭 すべて", key="chip_all", use_container_width=True, type="primary" if not active_chip else "secondary"):
        st.session_state.pending_fil_prio = []
        st.rerun()
for idx, p in enumerate(all_prio, start=1):
    with chip_cols[idx]:
        if st.button(
            f"{PRIORITY_BADGE.get(p, '⚪')} {p}: {prio_counts[p]}",
            key=f"chip_{p}",
            use_container_width=True,
            type="primary" if active_chip == p else "secondary",
        ):
            st.session_state.pending_fil_prio = [] if active_chip == p else [p]
            st.rerun()
with chip_cols[-1]:
    st.markdown(
        f'<div class="stat-chip" style="text-align:center;">📌 選択済: <b>{len(st.session_state.selected)}</b></div>',
        unsafe_allow_html=True,
    )

st.caption("キーボード操作は上の「記事選択」をフォーカスして ↑↓ で移動、Tab で「表示中を選択」に移って Enter で実行できます")

st.caption("記事リストはカーソルを合わせるか Tab で移動するとプレビューが切り替わります。チェックボックスでは Enter / Space で選択を切り替えられます。")
left_col, right_col = st.columns([1, 1], gap="large")
panel_height = 760

with left_col:
    if False and filtered_idx:
        st.selectbox(
            "記事選択",
            filtered_idx,
            key="preview_picker",
            format_func=lambda idx: format_article_picker_label(idx, articles[idx]),
            help="ここを選んでから ↑↓ キーで記事を移動できます",
        )
        if st.session_state.preview != st.session_state.preview_picker:
            st.session_state.preview = st.session_state.preview_picker
            st.rerun()

        nav1, nav2, nav3 = st.columns(3)
        current_pos = filtered_idx.index(st.session_state.preview)
        with nav1:
            if st.button("前の記事", use_container_width=True, disabled=current_pos == 0):
                st.session_state.preview = filtered_idx[max(current_pos - 1, 0)]
                st.session_state.preview_picker = st.session_state.preview
                st.rerun()
        with nav2:
            current_selected = st.session_state.preview in st.session_state.selected
            if st.button(
                "表示中を解除" if current_selected else "表示中を選択",
                use_container_width=True,
                key="toggle_current_preview",
            ):
                if current_selected:
                    st.session_state.selected.discard(st.session_state.preview)
                else:
                    st.session_state.selected.add(st.session_state.preview)
                sync_checkbox_state(filtered_idx)
                st.rerun()
        with nav3:
            if st.button("次の記事", use_container_width=True, disabled=current_pos == len(filtered_idx) - 1):
                st.session_state.preview = filtered_idx[min(current_pos + 1, len(filtered_idx) - 1)]
                st.session_state.preview_picker = st.session_state.preview
                st.rerun()

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("✔ 全選択", use_container_width=True):
            for i in filtered_idx:
                st.session_state.selected.add(i)
            sync_checkbox_state(filtered_idx)
            st.rerun()
    with b2:
        if st.button("✖ 全解除", use_container_width=True):
            st.session_state.selected -= set(filtered_idx)
            sync_checkbox_state(filtered_idx)
            st.rerun()
    with b3:
        if st.button("🔵 重要のみ", use_container_width=True, key="important_only"):
            for i in filtered_idx:
                if has_primary_company_keyword(articles[i].title, articles[i].body):
                    st.session_state.selected.add(i)
            sync_checkbox_state(filtered_idx)
            st.rerun()

    st.caption(f"表示中: {len(filtered)} 件")

    try:
        list_box = st.container(height=panel_height, border=False)
    except TypeError:
        list_box = st.container()

    ensure_checkbox_state(filtered_idx)
    with list_box:
        for i, a in zip(filtered_idx, filtered):
            badge = PRIORITY_BADGE.get(a.priority, "*")
            col_btn, col_cb = st.columns([0.92, 0.08])
            with col_btn:
                label = (
                    f"{badge} {a.title[:52]}{'…' if len(a.title) > 52 else ''}\n"
                    f"   {a.newspaper}  {a.date}  {a.page if a.nikkei_digital_flag else f'{a.page}P'}"
                    + ("  📄" if a.has_pdf else "")
                    + ("  🚩地方面" if a.local_flag else "")
                    + ("  💻日経電子版" if a.nikkei_digital_flag else "")
                )
                if st.button(label, key=f"prev_{i}", use_container_width=True, help="Preview", type="primary" if st.session_state.preview == i else "secondary"):
                    st.session_state.preview = i
                    st.session_state.preview_picker = i
                    st.rerun()
            with col_cb:
                st.checkbox(
                    "",
                    key=f"cb_{i}",
                    label_visibility="collapsed",
                    on_change=handle_checkbox_change,
                    args=(i,),
                )

with right_col:
    st.caption("プレビュー")
    try:
        preview_box = st.container(height=panel_height, border=False)
    except TypeError:
        preview_box = st.container()

    with preview_box:
        pi = st.session_state.preview
        if pi is not None and pi < len(articles):
            pa = articles[pi]
            color = PRIORITY_COLOR.get(pa.priority, "#9e9e9e")
            badge = PRIORITY_BADGE.get(pa.priority, "⚪")
            safe_title = highlight_matches(pa.title) if pa.title else "（タイトルなし）"
            safe_body = highlight_matches(pa.body) if pa.body else "<span style='color:#aaa'>（本文データなし）</span>"
            local_badge_style = "display:inline-block;" if pa.local_flag else "display:none;"
            nikkei_badge_style = "display:inline-block;" if pa.nikkei_digital_flag else "display:none;"
            page_text = pa.page if pa.nikkei_digital_flag else f"{pa.page}ページ"
            st.markdown(
                f"""
<div class="preview-card">
  <div class="preview-title">{safe_title}</div>
  <div class="preview-meta">
    <span class="badge" style="background:{color}">{badge} {pa.priority}</span>
    <span class="badge" style="background:#ef6c00;{local_badge_style}">🚩 地方面</span>
    <span class="badge" style="background:#455a64;{nikkei_badge_style}">💻 日経電子版</span>
    {pa.newspaper}　{pa.date}　{pa.edition}　{page_text}
    {"　📄 PDF有" if pa.has_pdf else ""}
    {"　" + pa.charcount + "文字" if pa.charcount else ""}
  </div>
  <div class="preview-body">{safe_body}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
<div class="preview-card preview-empty">
  <span style="font-size:48px">👈</span>
  <span style="font-size:13px">左の記事タイトルをクリックするとプレビューが表示されます</span>
</div>
""",
                unsafe_allow_html=True,
            )

if st.session_state.selected:
    st.markdown("---")
    st.markdown("## 📤 出力テーブル")

    sel_idx = sorted(st.session_state.selected)
    sel_arts = [articles[i] for i in sel_idx]
    df_cs = make_chiasapo_df(sel_arts)
    df_bm = make_bucho_df(sel_arts)
    today = datetime.now()
    date_mmdd = f"{today.month}/{today.day}"

    TO_CHEERFUL = [
        "髙橋あゆり/TakahashiAyuri (コーポレートサービス) <takahashi.ayuri.tj8@jx-nmm.com>",
        "齋藤敦子/SaitoAtsuko (コーポレートサービス) <saito.atsuko.vm2@jx-nmm.com>",
        "渋谷義勝/ShibuyaYoshikatsu (コーポレートサービス) <shibuya.yoshi.aa2@jx-nmm.com>",
        "野上勝也/NogamiKatsuya (コーポレートサービス) <nogami.katsuya.jq9@jx-nmm.com>",
        "T010-0-209-ＪＸ金属チアフルサポート室 <cheerful.support@jx-nmm.com>",
    ]
    CC_CHEERFUL = [
        "荒金宏/ArakaneHiroshi (本社 コーポレートコミュニケーション部) <arakane.hiroshi.dy9@jx-nmm.com>",
        "菅屋遥介/SugenoyaYosuke (本社 コーポレートコミュニケーション部) <sugenoya.yosuke.ga2@jx-nmm.com>",
        "重光泰智/ShigemitsuTaichi (本社 コーポレートコミュニケーション部) <shigemitsu.taich.sp4@jx-nmm.com>",
        "河合歩美/KawaiAyumi (本社 コーポレートコミュニケーション部) <kawai.ayumi.sm3@jx-nmm.com>",
        "齋藤晶/SaitoAki (本社 コーポレートコミュニケーション部) <saito.aki.gq9@jx-nmm.com>",
    ]
    TO_MANAGER = [
        "荒金宏/ArakaneHiroshi (本社 コーポレートコミュニケーション部) <arakane.hiroshi.dy9@jx-nmm.com>",
        "菅屋遥介/SugenoyaYosuke (本社 コーポレートコミュニケーション部) <sugenoya.yosuke.ga2@jx-nmm.com>",
    ]
    CC_MANAGER = [
        "江野本至/EnomotoItaru (本社 コーポレートコミュニケーション部) <enomoto.itaru.rw4@jx-nmm.com>",
        "河合歩美/KawaiAyumi (本社 コーポレートコミュニケーション部) <kawai.ayumi.sm3@jx-nmm.com>",
        "齋藤晶/SaitoAki (本社 コーポレートコミュニケーション部) <saito.aki.gq9@jx-nmm.com>",
        "重光泰智/ShigemitsuTaichi (本社 コーポレートコミュニケーション部) <shigemitsu.taich.sp4@jx-nmm.com>",
    ]

    cs_xlsx = to_excel_bytes(df_cs, "チアサポ表")
    bm_xlsx = to_excel_bytes(df_bm, "部長確認表")

    tab1, tab2, tab3, tab4 = st.tabs(["📋 チアサポ表", "📧 部長確認メール表", "⚙ 編集", "✉ メール出力"])

    with tab1:
        st.markdown("#### チアサポ送付用（日付降順 → 新聞社名降順）")
        st.caption("記事名が見切れにくいよう、折り返し表示のテーブルにしています")
        render_export_table(df_cs)
        if cs_xlsx is not None:
            st.download_button(
                "⬇️ Excel (.xlsx)",
                cs_xlsx,
                "チアサポ表.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "CSV (UTF-8 BOM)",
                df_cs.to_csv(index=False, encoding="utf-8-sig"),
                "チアサポ表_utf8.csv",
                "text/csv",
                use_container_width=True,
            )
        with c2:
            st.download_button(
                "CSV (Shift-JIS)",
                df_cs.to_csv(index=False, encoding="cp932", errors="replace"),
                "チアサポ表_sjis.csv",
                "text/csv",
                use_container_width=True,
            )
        st.text_area("Excel貼り付け用 TSV（全選択→コピー）", df_cs.to_csv(index=False, sep="\t"), height=140, key="tsv_cs")

    with tab2:
        st.markdown("#### 部長確認メール用（優先度順）")
        st.caption("配信:全● ／ ファイリング・12階:JX金属含む● ／ イントラ:産業・鉄鋼・電波新聞 かつ 自社名記載●")
        render_export_table(df_bm)
        if bm_xlsx is not None:
            st.download_button(
                "⬇️ Excel (.xlsx)",
                bm_xlsx,
                "部長確認表.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "CSV (UTF-8 BOM)",
                df_bm.to_csv(index=False, encoding="utf-8-sig"),
                "部長確認表_utf8.csv",
                "text/csv",
                use_container_width=True,
            )
        with c2:
            st.download_button(
                "CSV (Shift-JIS)",
                df_bm.to_csv(index=False, encoding="cp932", errors="replace"),
                "部長確認表_sjis.csv",
                "text/csv",
                use_container_width=True,
            )
        st.text_area("Excel貼り付け用 TSV（全選択→コピー）", df_bm.to_csv(index=False, sep="\t"), height=140, key="tsv_bm")

    with tab3:
        st.markdown("#### 優先度の手動変更")
        st.caption("優先度を変更すると部長確認表の順番・ファイリング等にも反映されます")
        edit_data = [
            {"タイトル": a.title, "新聞": a.newspaper, "日付": a.date, "優先度": a.priority, "_idx": idx}
            for idx, a in zip(sel_idx, sel_arts)
        ]
        edited = st.data_editor(
            pd.DataFrame(edit_data)[["タイトル", "新聞", "日付", "優先度"]],
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "優先度": st.column_config.SelectboxColumn(
                    options=["自社名・旧社名", "自社関連", "競合他社", "業界関連", "ESG関連", "未分類"]
                )
            },
            key="editor",
        )
        if st.button("✅ 変更を反映", type="primary"):
            for row_i, row in edited.iterrows():
                articles[edit_data[row_i]["_idx"]].priority = row["優先度"]
            st.success("反映しました")
            st.rerun()

    with tab4:
        st.markdown("#### Outlook用メールファイル（.msg）")
        sender_name = st.text_input("送信者名（本文末尾）", value="", placeholder="例: 重光")

        cheerful_subject = f"{date_mmdd} クリッピングタイトルリスト送付"
        cheerful_body = (
            "齋藤(敦)様、渋谷様、野上様、チアフルサポート室ご担当者様\n\n"
            "お疲れ様です。\n"
            "本日のタイトルリストをお送りいたします。\n"
            "どうぞよろしくお願いいたします。\n\n"
            f"{sender_name}".rstrip()
        )

        manager_subject = f"（広報内）{date_mmdd} クリッピングタイトルリストの送付"
        manager_body_text = (
            "荒金様、菅屋様\n\n"
            "本日のクリッピングタイトルリストを送付いたします。\n"
            "のちほど、チアサポからのPDF送付がありましたら以下配信要否等についてご判断いただけますと幸いです。\n\n"
            + df_bm.to_string(index=False)
            + "\n\n"
            + f"{sender_name}".rstrip()
        )
        manager_table_html = df_bm.to_html(index=False, border=1)
        manager_body_html = f"""
<html><body>
<p>荒金様、菅屋様</p>
<p>本日のクリッピングタイトルリストを送付いたします。<br>
のちほど、チアサポからのPDF送付がありましたら以下配信要否等についてご判断いただけますと幸いです。</p>
{manager_table_html}
<p>{escape(sender_name)}</p>
</body></html>
"""

        cheerful_attachments: list[tuple[str, bytes, str]] = []
        if cs_xlsx is not None:
            cheerful_attachments.append(
                ("チアサポ表.xlsx", cs_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            )
        cheerful_msg, cheerful_msg_error = build_msg_bytes_via_outlook(
            subject=cheerful_subject,
            to_list=TO_CHEERFUL,
            cc_list=CC_CHEERFUL,
            body_text=cheerful_body,
            attachments=cheerful_attachments,
        )

        manager_attachments: list[tuple[str, bytes, str]] = []
        if bm_xlsx is not None:
            manager_attachments.append(
                ("部長確認表.xlsx", bm_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            )
        manager_msg, manager_msg_error = build_msg_bytes_via_outlook(
            subject=manager_subject,
            to_list=TO_MANAGER,
            cc_list=CC_MANAGER,
            body_text=manager_body_text,
            body_html=manager_body_html,
            attachments=manager_attachments,
        )

        if cheerful_msg is not None and manager_msg is not None:
            st.download_button(
                "① チアサポ送信用 .msg",
                cheerful_msg,
                f"チアサポ送信_{today.strftime('%Y%m%d')}.msg",
                "application/vnd.ms-outlook",
                use_container_width=True,
            )
            st.download_button(
                "② 部長確認送信用 .msg",
                manager_msg,
                f"部長確認送信_{today.strftime('%Y%m%d')}.msg",
                "application/vnd.ms-outlook",
                use_container_width=True,
            )
        else:
            msg_errors = [err for err in [cheerful_msg_error, manager_msg_error] if err]
            error_detail = f" Details: {' / '.join(msg_errors)}" if msg_errors else ""
            st.warning(f"MSG generation failed. Please use EML format.{error_detail}")
            cheerful_eml = build_eml_bytes(
                subject=cheerful_subject,
                to_list=TO_CHEERFUL,
                cc_list=CC_CHEERFUL,
                body_text=cheerful_body,
                attachments=cheerful_attachments,
            )
            manager_eml = build_eml_bytes(
                subject=manager_subject,
                to_list=TO_MANAGER,
                cc_list=CC_MANAGER,
                body_text=manager_body_text,
                body_html=manager_body_html,
                attachments=manager_attachments,
            )
            st.download_button(
                "① チアサポ送信用 .eml",
                cheerful_eml,
                f"チアサポ送信_{today.strftime('%Y%m%d')}.eml",
                "message/rfc822",
                use_container_width=True,
            )
            st.download_button(
                "② 部長確認送信用 .eml",
                manager_eml,
                f"部長確認送信_{today.strftime('%Y%m%d')}.eml",
                "message/rfc822",
                use_container_width=True,
            )
