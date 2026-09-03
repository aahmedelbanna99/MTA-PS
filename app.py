# -*- coding: utf-8 -*-
# MTA Portsaid - Live Riders
# تطبيق Streamlit لعرض حالة الطيارين المباشرة (مدينة 204 - بورسعيد)
# مع تجديد تلقائي للتوكن عبر tokens.json

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh

# ==================== إعدادات الصفحة والتحديث التلقائي ====================
st.set_page_config(page_title="MTA Portsaid", layout="wide")
st_autorefresh(interval=60000, key="datarefresh")

st.markdown(
    """
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==================== الرأس ====================
header_cols = st.columns([1, 12])
with header_cols[0]:
    # لو اللوجو مش موجود أو تالف، نكمل من غير ما التطبيق يبوظ
    try:
        st.image("talabat.jpeg", width=120)
    except Exception:
        pass
with header_cols[1]:
    st.markdown(
        '<h1 style="margin-bottom:0;">MTA Portsaid - Live Riders</h1>',
        unsafe_allow_html=True,
    )

# ==================== حماية الدخول (باسورد لأكتر من مشرف) ====================
# القيمة في secrets.toml بتاخد كذا باسورد مفصولين بفاصلة، مثال:
# SUPERVISOR_PASSWORDS = "باسورد_الأول,باسورد_التاني"
SUPERVISOR_PASSWORDS = [
    p.strip()
    for p in st.secrets.get("SUPERVISOR_PASSWORDS", "").split(",")
    if p.strip()
]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("### 🔒 Sign in")
    login_pwd = st.text_input("Password", type="password", key="login_pwd_input")
    if st.button("Go", key="login_submit_btn"):
        if SUPERVISOR_PASSWORDS and login_pwd in SUPERVISOR_PASSWORDS:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Wrong password")
    st.stop()

# ==================== التوكنات (tokens.json له الأولوية) ====================
TOKENS_FILE = "tokens.json"
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

# القيم الافتراضية من st.secrets إن وجدت
TOKENS = {
    "BEARER_TOKEN": st.secrets.get("BEARER_TOKEN", ""),
    "DHH_TOKEN": st.secrets.get("DHH_TOKEN", ""),
    "REFRESH_TOKEN": st.secrets.get("REFRESH_TOKEN", ""),
    # كوكيز Cloudflare (تبدأ من secrets، لكن ممكن تتحدث من لوحة الأدمن وتتحفظ في tokens.json)
    "CF_APP_SESSION": st.secrets.get("CF_APP_SESSION", ""),
    "CF_AUTHORIZATION": st.secrets.get("CF_AUTHORIZATION", ""),
}

# tokens.json له الأولوية على st.secrets (بما فيها كوكيز Cloudflare بعد التعديل)
if os.path.exists(TOKENS_FILE):
    try:
        with open(TOKENS_FILE, "r") as f:
            saved = json.load(f)
        for k in (
            "BEARER_TOKEN",
            "DHH_TOKEN",
            "REFRESH_TOKEN",
            "CF_APP_SESSION",
            "CF_AUTHORIZATION",
        ):
            if saved.get(k):
                TOKENS[k] = saved[k]
    except Exception:
        pass


def save_tokens():
    # حفظ التوكنات في tokens.json
    try:
        with open(TOKENS_FILE, "w") as f:
            json.dump(TOKENS, f)
    except Exception:
        pass


def build_headers():
    # بناء الهيدرز من قاموس التوكنات الحالي (مع كوكيز Cloudflare زي النسخة الأصلية)
    return {
        "Authorization": f"Bearer {TOKENS['BEARER_TOKEN']}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Cookie": (
            f"CF_AppSession={TOKENS['CF_APP_SESSION']}; "
            f"CF_Authorization={TOKENS['CF_AUTHORIZATION']}; "
            f"dhh_token={TOKENS['DHH_TOKEN']}; "
            f"refresh_token={TOKENS['REFRESH_TOKEN']}"
        ),
    }


def refresh_access_token():
    # تجديد التوكن — بنجرب الأول من غير كوكيز Cloudflare
    # لو نجح من غيرهم، يبقى التطبيق بيتعالج لوحده حتى لو الكوكيز ماتت
    for use_cf in (False, True):
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            }
            cookies = (
                f"dhh_token={TOKENS['DHH_TOKEN']}; refresh_token={TOKENS['REFRESH_TOKEN']}"
            )
            if use_cf:
                cookies += (
                    f"; CF_AppSession={TOKENS['CF_APP_SESSION']}"
                    f"; CF_Authorization={TOKENS['CF_AUTHORIZATION']}"
                )
            headers["Cookie"] = cookies

            resp = requests.post(
                "https://eg.me.logisticsbackoffice.com/api/iam-login/auth/refresh_token",
                json={"refresh_token": TOKENS["REFRESH_TOKEN"]},
                headers=headers,
                timeout=30,
            )
            if resp.status_code in (200, 201) and "application/json" in resp.headers.get("Content-Type", ""):
                data = resp.json()
                new_dhh = data.get("dhhToken")
                if not new_dhh:
                    continue
                if data.get("token"):
                    TOKENS["BEARER_TOKEN"] = data["token"]
                TOKENS["DHH_TOKEN"] = new_dhh
                if data.get("refreshToken"):
                    TOKENS["REFRESH_TOKEN"] = data["refreshToken"]
                save_tokens()
                st.toast("✅ تم تحديث التوكنات تلقائيًا")
                return True
        except Exception:
            continue
    return False


def fetch_with_auth(url, params):
    # طلب مع إعادة المحاولة عند 401 (تجديد التوكن ثم إعادة المحاولة)
    resp = None
    for attempt in range(2):
        resp = requests.get(url, headers=build_headers(), params=params, timeout=30)
        if resp.status_code == 401 and attempt == 0:
            if refresh_access_token():
                continue
        return resp
    return resp


# ==================== دوال جلب البيانات ====================
@st.cache_data(ttl=60)
def get_riders():
    # جلب قائمة الطيارين من كل الصفحات (الـ API يعيد طيارين مكتب المستخدم فقط)
    url = (
        "https://eg.me.logisticsbackoffice.com/"
        "api/rider-live-operations/v1/external/city/204/riders"
    )

    all_riders = []
    page = 0
    while True:
        resp = fetch_with_auth(url, {"page": page, "size": 100})
        if resp.status_code != 200:
            st.error(f"خطأ في جلب الطيارين: {resp.status_code} (صفحة {page})")
            with st.expander("🔍 تفاصيل الرد (للتشخيص)"):
                st.code(f"URL: {resp.url}")
                st.code(f"Status: {resp.status_code}")
                st.code(f"Response headers Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
                st.code(resp.text[:800])
            break
        try:
            data = resp.json()
        except Exception:
            st.error("❌ الـ API رجّع HTML بدل JSON — جلسة Cloudflare منتهية")
            st.markdown(
                "**الحل:** اضغط زرار 🔒 Admin تحت وحدّث الكوكيز الجديدة."
            )
            with st.expander("🔍 تفاصيل الرد (للتشخيص)"):
                st.code(f"URL: {resp.url}")
                st.code(f"Status: {resp.status_code}")
                st.code(resp.text[:600])
            break

        if isinstance(data, dict):
            batch = data.get("content") or data.get("data") or []
        elif isinstance(data, list):
            batch = data
        else:
            batch = []

        all_riders.extend(batch)

        # لو الصفحة أقل من 100 يبقى دي آخر صفحة
        if len(batch) < 100:
            break
        page += 1
        if page > 20:  # حماية من حلقة لا نهائية
            break

    return all_riders


@st.cache_data(ttl=300)
def get_tomorrow_shifts(rider_ids):
    # جلب شيفتات الغد لكل مندوب بالتوازي (10 في نفس الوقت بدل واحد ورا التاني)
    # بنستخدم fetch_with_auth اللي بيعالج الـ 401 بنفسه — من غير refresh يدوي مكرر
    cairo_tz = ZoneInfo("Africa/Cairo")
    tomorrow = datetime.now(cairo_tz) + timedelta(days=1)
    params = {
        "city_id": 204,
        "start_at": tomorrow.strftime("%Y-%m-%dT00:00:00.000Z"),
        "end_at": tomorrow.strftime("%Y-%m-%dT23:59:59.999Z"),
    }
    base = "https://eg.me.logisticsbackoffice.com/api/rooster/v3/employees"

    def check(rid):
        try:
            resp = fetch_with_auth(f"{base}/{int(rid)}/shifts", params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            shifts = []
            if isinstance(data, dict):
                shifts = data.get("content") or data.get("data") or []
            elif isinstance(data, list):
                shifts = data
            return int(rid) if shifts else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(check, rider_ids))
    return {r for r in results if r}


# ==================== حالة الطيار ====================
def get_status_info(raw_status):
    # تطبيع حالة الطيار وتحويلها إلى عرض ملوّن
    s = (raw_status or "").strip().lower().replace(" ", "_")
    if s == "working":
        return "Working 🟢"
    if s == "break":
        return "Break 🟡"
    if s in ("temp_offline", "offline", "not_working"):
        return "Temp Offline 🟡"
    if s == "ending":
        return "Ending ⚪"
    if s == "late":
        return "Late 🔴"
    if s == "starting":
        return "Starting 🔵"
    return "Starting 🔵"


# ==================== جلب البيانات ====================
riders = get_riders()

# جلب مناديب الغد من اللايف نفسه (نفس endpoint المتصفح)
rider_ids = []
rider_names_by_id = {}
for r in riders:
    rid = r.get("employee_id") or r.get("employeeId") or r.get("id")
    try:
        rid_int = int(rid)
        rider_ids.append(rid_int)
        rider_names_by_id[rid_int] = (
            r.get("name") or r.get("rider_name") or r.get("riderName") or "Unknown"
        )
    except (TypeError, ValueError):
        pass

# نتأكد من شيفت بكرة لكل المناديب الظاهرين على الخريطة دلوقتي
tomorrow_rider_ids = get_tomorrow_shifts(rider_ids)
st.caption(f"📅 شيفتات بكرة: {len(tomorrow_rider_ids)} مندوب ليهم شيفت")

missing_core = [rid for rid in rider_ids if rid not in tomorrow_rider_ids]

# ==================== زر التحديث + لوحة الأدمن (مخفية إلا برابط سري) ====================
# لوحة الأدمن بتظهر بس لو الرابط فيه ?admin=1 في الآخر
is_admin_url = st.query_params.get("admin") == "1"

if is_admin_url:
    top_cols = st.columns([1, 1, 1, 1, 1, 3])
    with top_cols[0]:
        if st.button("🔄 Refresh"):
            st.cache_data.clear()
            st.rerun()
    with top_cols[1]:
        if st.button("🔒 Admin"):
            st.session_state.show_admin = not st.session_state.get("show_admin", False)
    with top_cols[2]:
        if st.button("🌐 IP"):
            try:
                ip_resp = requests.get("https://api.ipify.org?format=json", timeout=10)
                st.info(f"IP بتاع السيرفر: {ip_resp.json().get('ip')}")
            except Exception as e:
                st.error(f"مقدرش أجيب الـ IP: {e}")
    with top_cols[3]:
        if st.button("🔍 Raw Data"):
            sample = None
            for r in riders:
                di = r.get("deliveries_info") or {}
                if di.get("has_active_deliveries"):
                    sample = r
                    break
            if not sample and riders:
                sample = riders[0]
            if sample:
                st.json(sample)
            else:
                st.warning("مفيش بيانات طيارين لعرضها دلوقتي")
    with top_cols[4]:
        if st.button("🔑 Tokens"):
            def mask(v):
                if not v:
                    return "(فاضي)"
                return f"{v[:15]}...{v[-15:]} (طول: {len(v)})"
            st.code(
                f"BEARER_TOKEN: {mask(TOKENS.get('BEARER_TOKEN'))}\n"
                f"DHH_TOKEN: {mask(TOKENS.get('DHH_TOKEN'))}\n"
                f"CF_AUTHORIZATION: {mask(TOKENS.get('CF_AUTHORIZATION'))}\n"
                f"CF_APP_SESSION: {mask(TOKENS.get('CF_APP_SESSION'))}\n"
                f"REFRESH_TOKEN: {mask(TOKENS.get('REFRESH_TOKEN'))}\n"
                f"tokens.json موجود: {os.path.exists(TOKENS_FILE)}"
            )
        if st.button("🗑️ امسح tokens.json"):
            try:
                if os.path.exists(TOKENS_FILE):
                    os.remove(TOKENS_FILE)
                    st.success("✅ اتمسح! دوس Refresh دلوقتي")
                else:
                    st.info("مش موجود أصلاً")
            except Exception as e:
                st.error(f"مقدرش أمسحه: {e}")
        if st.button("🔁 جرب Refresh Token يدوي"):
            for use_cf in (False, True):
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                }
                cookies = (
                    f"dhh_token={TOKENS['DHH_TOKEN']}; refresh_token={TOKENS['REFRESH_TOKEN']}"
                )
                if use_cf:
                    cookies += (
                        f"; CF_AppSession={TOKENS['CF_APP_SESSION']}"
                        f"; CF_Authorization={TOKENS['CF_AUTHORIZATION']}"
                    )
                headers["Cookie"] = cookies
                try:
                    resp = requests.post(
                        "https://eg.me.logisticsbackoffice.com/api/iam-login/auth/refresh_token",
                        json={"refresh_token": TOKENS["REFRESH_TOKEN"]},
                        headers=headers,
                        timeout=30,
                    )
                    st.write(f"محاولة {'مع' if use_cf else 'من غير'} كوكيز Cloudflare:")
                    st.code(f"Status: {resp.status_code}")
                    st.code(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
                    st.code(resp.text[:1000])
                except Exception as e:
                    st.error(f"Exception: {e}")
                st.divider()
else:
    # الوضع العادي: زرار الريفريش بس، من غير أي إشارة لوجود لوحة أدمن
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

if is_admin_url and st.session_state.get("show_admin", False):
    with st.container(border=True):
        if not st.session_state.get("admin_authed", False):
            st.markdown("**دخول الأدمن**")
            pwd = st.text_input(
                "كلمة السر", type="password", key="admin_pwd_input"
            )
            if st.button("دخول", key="admin_login_btn"):
                if ADMIN_PASSWORD and pwd == ADMIN_PASSWORD:
                    st.session_state.admin_authed = True
                    st.rerun()
                else:
                    st.error("❌ كلمة السر غلط")
        else:
            st.success("✅ مسجل دخول كأدمن")
            st.caption("الصق القيم الجديدة اللي جبتها من المتصفح بعد تسجيل الدخول (اسيب أي خانة فاضية لو مش عايز تحدثها)")
            new_bearer = st.text_area(
                "BEARER_TOKEN الجديد",
                key="new_bearer",
                height=100,
            )
            new_dhh = st.text_area(
                "DHH_TOKEN الجديد",
                key="new_dhh",
                height=100,
            )
            new_cf_session = st.text_input(
                "CF_AppSession الجديد", key="new_cf_session"
            )
            new_cf_auth = st.text_area(
                "CF_Authorization الجديد", key="new_cf_auth", height=100
            )
            btn_cols = st.columns(2)
            with btn_cols[0]:
                if st.button("💾 حفظ وتحديث", key="save_cookies_btn"):
                    updated = False
                    if new_bearer.strip():
                        TOKENS["BEARER_TOKEN"] = new_bearer.strip()
                        updated = True
                    if new_dhh.strip():
                        TOKENS["DHH_TOKEN"] = new_dhh.strip()
                        updated = True
                    if new_cf_session.strip():
                        TOKENS["CF_APP_SESSION"] = new_cf_session.strip()
                        updated = True
                    if new_cf_auth.strip():
                        TOKENS["CF_AUTHORIZATION"] = new_cf_auth.strip()
                        updated = True
                    if updated:
                        save_tokens()
                        st.cache_data.clear()
                        st.success("✅ تم تحديث القيم بنجاح")
                        st.session_state.show_admin = False
                        st.session_state.admin_authed = False
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("⚠️ محتاج تحط قيمة واحدة على الأقل")
            with btn_cols[1]:
                if st.button("🚪 خروج", key="admin_logout_btn"):
                    st.session_state.show_admin = False
                    st.session_state.admin_authed = False
                    st.rerun()

            st.divider()
            st.caption("🔍 تشخيص مؤقت: شوف شكل بيانات شيفت مندوب في يوم معين")
            debug_rid = st.text_input("Employee ID", key="debug_shift_rid")
            debug_date = st.text_input(
                "التاريخ (YYYY-MM-DD)", value="2026-09-01", key="debug_shift_date"
            )
            if st.button("جيب بيانات الشيفت", key="debug_shift_btn"):
                if debug_rid.strip():
                    cairo_tz = ZoneInfo("Africa/Cairo")
                    day_start = datetime.strptime(
                        debug_date.strip(), "%Y-%m-%d"
                    ).replace(tzinfo=cairo_tz)
                    day_end = day_start + timedelta(days=1, seconds=-1)
                    start_utc = day_start.astimezone(ZoneInfo("UTC"))
                    end_utc = day_end.astimezone(ZoneInfo("UTC"))
                    debug_url = (
                        f"https://eg.me.logisticsbackoffice.com/api/rooster/v3/"
                        f"employees/{debug_rid.strip()}/shifts"
                    )
                    debug_resp = fetch_with_auth(
                        debug_url,
                        {
                            "city_id": 204,
                            "start_at": start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "end_at": end_utc.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
                        },
                    )
                    st.code(f"Status: {debug_resp.status_code}")
                    try:
                        st.json(debug_resp.json())
                    except Exception:
                        st.code(debug_resp.text[:1500])
                else:
                    st.warning("حط Employee ID الأول")

# ==================== عدّ الطيارين (بدون فلترة مكتب) ====================
total_riders = 0
working_count = 0
break_count = 0
temp_offline_count = 0
starting_count = 0
late_count = 0
with_order_count = 0
without_order_count = 0
break_riders = []

for r in riders:
    total_riders += 1
    status_info = get_status_info(r.get("status"))

    if status_info == "Working 🟢":
        working_count += 1
    elif status_info == "Break 🟡":
        break_count += 1
        break_riders.append(r)
    elif status_info == "Temp Offline 🟡":
        temp_offline_count += 1
    elif status_info == "Starting 🔵":
        starting_count += 1
    elif status_info == "Late 🔴":
        late_count += 1

    deliveries_info = r.get("deliveries_info") or {}
    if deliveries_info.get("has_active_deliveries"):
        with_order_count += 1
    else:
        without_order_count += 1

# ==================== التبويبات ====================
live_map_tab, all_breaks_tab, unassigned_tab = st.tabs(
    ["🗺️ Live Map", "☕ All Breaks", "📋 Unassigned"]
)


def rider_matches_filter(r, filt):
    # هل الطيار مطابق للفلتر المختار؟
    if filt == "all":
        return True
    status_info = get_status_info(r.get("status"))
    deliveries_info = r.get("deliveries_info") or {}
    has_active = deliveries_info.get("has_active_deliveries", False)
    if filt == "working":
        return status_info == "Working 🟢"
    if filt == "late":
        return status_info == "Late 🔴"
    if filt == "break":
        return status_info in ("Break 🟡", "Temp Offline 🟡")
    if filt == "starting":
        return status_info == "Starting 🔵"
    if filt == "with_order":
        return has_active
    if filt == "without_order":
        return not has_active
    return True


# ==================== تبويب الخريطة ====================
with live_map_tab:
    # ---- شرائط الفلترة (Pills) بشكل غامق ----
    st.markdown(
        """
        <style>
            div[data-testid="stPills"] button {
                background-color: #F7F7F5 !important;
                border: none !important;
                border-radius: 20px !important;
                color: #1a1a1a !important;
            }
            div[data-testid="stPills"] button[aria-checked="true"] {
                background-color: #1a1a1a !important;
                color: #fff !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    filter_options = [
        ("all", f"الكل · {total_riders}"),
        ("working", f"🟢 Working · {working_count}"),
        ("late", f"🔴 Late · {late_count}"),
        ("break", f"🟡 Break · {break_count}"),
        ("starting", f"🔵 Starting · {starting_count}"),
        ("with_order", f"📦 With order · {with_order_count}"),
        ("without_order", f"⚪ Without order · {without_order_count}"),
    ]
    filter_keys = [f[0] for f in filter_options]
    filter_labels = {f[0]: f[1] for f in filter_options}

    if hasattr(st, "pills"):
        selected_filter = st.pills(
            "فلترة الخريطة",
            options=filter_keys,
            format_func=lambda k: filter_labels[k],
            default="all",
            label_visibility="collapsed",
            key="map_filter_pills",
        )
        if not selected_filter:
            selected_filter = "all"
    else:
        # نسخة بديلة (fallback) لو الإصدار قديم ومفيهوش st.pills
        if "map_filter_fallback" not in st.session_state:
            st.session_state.map_filter_fallback = "all"
        pill_cols = st.columns(len(filter_options))
        for i, (key, label) in enumerate(filter_options):
            with pill_cols[i]:
                if st.button(label, key=f"pill_{key}"):
                    st.session_state.map_filter_fallback = key
        selected_filter = st.session_state.map_filter_fallback

    filtered_riders = [r for r in riders if rider_matches_filter(r, selected_filter)]

    m = folium.Map(location=[31.2653, 32.3019], zoom_start=13)
    points = []

    for r in filtered_riders:
        loc = r.get("location") or r.get("current_location") or {}
        lat = r.get("lat") or r.get("latitude") or loc.get("lat") or loc.get("latitude")
        lng = r.get("lng") or r.get("longitude") or loc.get("lng") or loc.get("longitude")
        if lat is None or lng is None:
            continue
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            continue
        if lat == 0 or lng == 0:
            continue

        status_info = get_status_info(r.get("status"))
        deliveries_info = r.get("deliveries_info") or {}
        has_active = deliveries_info.get("has_active_deliveries", False)

        rider_id = (
            r.get("employee_id")
            or r.get("employeeId")
            or r.get("id")
        )
        try:
            has_shift_tomorrow = int(rider_id) in tomorrow_rider_ids
        except (TypeError, ValueError):
            has_shift_tomorrow = False

        name = (
            r.get("name")
            or r.get("rider_name")
            or r.get("riderName")
            or "Unknown"
        )

        popup_html = f"""
        <div style="font-family:Arial; font-size:13px;">
            <b>Name:</b> {r.get('name', 'N/A')}<br>
            <b>Rider ID:</b> {rider_id}<br>
            <b>Status:</b> {status_info}<br>
            <b>Wallet:</b> {(r.get('wallet_info') or {}).get('balance', 'N/A')}<br>
            <b>Has Active Order:</b> {'Yes 🟢' if has_active else 'No 🔴'}<br>
            <b>Tomorrow Shift:</b> {'Yes ✅' if has_shift_tomorrow else 'No ❌'}<br>
            <b>Completed Orders:</b> {deliveries_info.get('completed_deliveries_count', deliveries_info.get('completed_deliveries', r.get('completed_orders', 0)))}<br>
            <b>Accepted Orders:</b> {deliveries_info.get('accepted_deliveries_count', deliveries_info.get('accepted_deliveries', r.get('accepted_orders', 0)))}
        </div>
        """

        if status_info == "Working 🟢":
            inner_color = "white" if has_active else "red"
            icon_html = f"""
            <div style="
                width: 24px; height: 24px;
                background: #65A30D;
                border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg);
                display: flex; align-items: center; justify-content: center;
            ">
                <div style="
                    width: 12px; height: 12px;
                    background: {inner_color};
                    border-radius: 50%;
                    transform: rotate(45deg);
                "></div>
            </div>
            """
            folium.Marker(
                [lat, lng],
                icon=folium.DivIcon(html=icon_html, icon_size=(24, 24)),
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{name} ({rider_id})",
            ).add_to(m)
        elif status_info == "Break 🟡" or status_info == "Temp Offline 🟡":
            icon_html = """
            <div style="
                width: 24px; height: 24px;
                background: #FACC15;
                border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg);
                display: flex; align-items: center; justify-content: center;
            ">
                <div style="
                    width: 12px; height: 12px;
                    background: white;
                    border-radius: 50%;
                    transform: rotate(45deg);
                "></div>
            </div>
            """
            folium.Marker(
                [lat, lng],
                icon=folium.DivIcon(html=icon_html, icon_size=(24, 24)),
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{name} ({rider_id})",
            ).add_to(m)
        else:
            color_map = {
                "Late 🔴": "red",
                "Starting 🔵": "blue",
                "Ending ⚪": "gray",
            }
            folium.Marker(
                [lat, lng],
                icon=folium.Icon(
                    color=color_map.get(status_info, "blue"),
                    icon="user",
                    prefix="fa",
                ),
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{name} ({rider_id})",
            ).add_to(m)

        points.append([lat, lng])

    if len(points) == 1:
        m.location = points[0]
        m.zoom_start = 17
    elif points:
        m.fit_bounds(points)

    st_folium(m, use_container_width=True, height=700, key="riders_map", returned_objects=[])

    st.markdown("© 2026 Created by Ahmed Elbanna")

# ==================== تبويب البريكات ====================
FORM_BASE = "https://docs.google.com/forms/d/e/1FAIpQLSekxaUQPgiWq-Y_IPfsTx7wTANA324a2JklFJZ_Gxg9CGPaKA/viewform"

def make_break_url(rid):
    return FORM_BASE + "?" + urlencode({
        "entry.302135773": "شيفتات الطيارين",
        "entry.1941659317": "فك بريك",
        "entry.264752225": str(rid),
        "entry.1551386634": "بورسعيد",
        "usp": "pp_url",
    })

with all_breaks_tab:
    if break_riders:
        st.write(f"☕ Riders on break: **{len(break_riders)}**")
        for r in break_riders:
            rid = r.get("employee_id") or r.get("employeeId") or r.get("id")
            name = r.get("name") or r.get("rider_name") or r.get("riderName") or "Unknown"
            status = get_status_info(r.get("status"))
            c1, c2, c3, c4 = st.columns([1.2, 3, 1.2, 1.2])
            with c1:
                st.write(f"**{rid}**")
            with c2:
                st.write(name)
            with c3:
                st.write(status)
            with c4:
                st.link_button("🔓 فك بريك", make_break_url(rid), use_container_width=True)
            st.divider()
    else:
        st.info("🟢 No riders are currently on break.")

with unassigned_tab:
    if not rider_ids:
        st.info("مفيش مناديب ظاهرين دلوقتي على الخريطة عشان نتأكد من شيفتهم بكرة")
    elif not missing_core:
        st.success("✅ كل المناديب الظاهرين دلوقتي حاططين شيفت بكرة")
    else:
        st.write(f"المناديب الي مش حاجزه شيفت بكره : {len(missing_core)}")
        rows_html = "".join(
            f"<tr><td style='text-align:center; padding:8px 16px; border-bottom:1px solid #ddd;'>{rid}</td>"
            f"<td style='text-align:center; padding:8px 16px; border-bottom:1px solid #ddd; white-space:nowrap;'>{rider_names_by_id.get(rid, 'مش معروف الاسم')}</td></tr>"
            for rid in missing_core
        )
        table_html = f"""
        <table style="border-collapse:collapse; font-family:Arial, sans-serif; font-size:14px; width:auto;">
            <thead>
                <tr>
                    <th style="text-align:center; padding:8px 16px; border-bottom:2px solid #999; width:100px;">ID</th>
                    <th style="text-align:center; padding:8px 16px; border-bottom:2px solid #999; white-space:nowrap;">Name</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        """
        st.markdown(table_html, unsafe_allow_html=True)

