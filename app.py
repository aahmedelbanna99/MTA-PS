# -*- coding: utf-8 -*-
# MTA Portsaid - Live Riders
# تطبيق Streamlit لعرض حالة الطيارين المباشرة (مدينة 204 - بورسعيد)
# مع تجديد تلقائي للتوكن عبر tokens.json

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
    st.image("talabat.jpeg", width=120)
with header_cols[1]:
    st.markdown(
        '<h1 style="margin-bottom:0;">MTA Portsaid - Live Riders</h1>',
        unsafe_allow_html=True,
    )

# ==================== التوكنات (tokens.json له الأولوية) ====================
TOKENS_FILE = "tokens.json"

# القيم الافتراضية من st.secrets إن وجدت
TOKENS = {
    "BEARER_TOKEN": st.secrets.get("BEARER_TOKEN", ""),
    "DHH_TOKEN": st.secrets.get("DHH_TOKEN", ""),
    "REFRESH_TOKEN": st.secrets.get("REFRESH_TOKEN", ""),
    # كوكيز Cloudflare (من secrets دايمًا — مبتتحدثش تلقائي)
    "CF_APP_SESSION": st.secrets.get("CF_APP_SESSION", ""),
    "CF_AUTHORIZATION": st.secrets.get("CF_AUTHORIZATION", ""),
}

# tokens.json له الأولوية على st.secrets
if os.path.exists(TOKENS_FILE):
    try:
        with open(TOKENS_FILE, "r") as f:
            saved = json.load(f)
        for k in ("BEARER_TOKEN", "DHH_TOKEN", "REFRESH_TOKEN"):
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
    # تجديد التوكن باستخدام REFRESH_TOKEN (بنفس شكل الطلب الأصلي: كوكيز + يوزر إيجنت)
    try:
        refresh_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Cookie": (
                f"CF_AppSession={TOKENS['CF_APP_SESSION']}; "
                f"CF_Authorization={TOKENS['CF_AUTHORIZATION']}; "
                f"dhh_token={TOKENS['DHH_TOKEN']}; "
                f"refresh_token={TOKENS['REFRESH_TOKEN']}"
            ),
        }
        resp = requests.post(
            "https://eg.me.logisticsbackoffice.com/api/iam-login/auth/refresh_token",
            json={"refresh_token": TOKENS["REFRESH_TOKEN"]},
            headers=refresh_headers,
            timeout=30,
        )

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            st.error("❌ Refresh رجّع HTML بدل JSON — جلسة Cloudflare انتهت (حدث CF_AppSession و CF_Authorization).")
            return False

        if resp.status_code not in (200, 201):
            st.error(f"❌ Refresh فشل برمز {resp.status_code}")
            st.code(resp.text[:800])
            return False

        data = resp.json()
        new_token = data.get("token")
        new_dhh = data.get("dhhToken")
        new_refresh = data.get("refreshToken")

        if not new_dhh:
            st.error("❌ الرد مفيهوش dhhToken جديد")
            return False

        if new_token:
            TOKENS["BEARER_TOKEN"] = new_token
        TOKENS["DHH_TOKEN"] = new_dhh
        if new_refresh:
            TOKENS["REFRESH_TOKEN"] = new_refresh

        save_tokens()
        st.toast("✅ تم تحديث التوكنات تلقائيًا")
        return True

    except Exception as e:
        st.error(f"❌ Token Refresh Error: {e}")
        return False


def fetch_with_auth(url, params):
    # طلب مع إعادة المحاولة عند 401 (تجديد التوكن ثم إعادة المحاولة)
    for attempt in range(2):
        resp = requests.get(url, headers=build_headers(), params=params, timeout=30)
        if resp.status_code == 401 and attempt == 0:
            if refresh_access_token():
                st.cache_data.clear()
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
            break
        try:
            data = resp.json()
        except Exception:
            st.error("فشل في تحليل استجابة الـ API (HTML بدل JSON).")
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
def get_tomorrow_shifts():
    # جلب شيفتات الغد بتوقيت القاهرة
    url = (
        "https://eg.me.logisticsbackoffice.com/"
        "api/rooster/v3/shifts"
    )
    cairo_tz = ZoneInfo("Africa/Cairo")
    now = datetime.now(cairo_tz)
    tomorrow = now + timedelta(days=1)
    params = {
        "city_id": 204,
        "distinct_day_plans": "false",
        "start_at": tomorrow.strftime("%Y-%m-%dT00:00:00.000Z"),
        "end_at": tomorrow.strftime("%Y-%m-%dT23:59:59.999Z"),
        "page": 0,
        "size": 100,
        "with_evaluations": "true",
        "with_field": "id_number",
        "with_time_zone": "Africa/Cairo",
    }
    resp = fetch_with_auth(url, params)
    if resp.status_code != 200:
        st.warning(f"⚠️ Shifts API رجّع {resp.status_code} — فحص شيفتات بكرة مش شغال")
        st.code(resp.text[:500])
        return []
    try:
        if "html" in resp.headers.get("Content-Type", "").lower():
            st.warning("⚠️ Shifts API رجّع HTML — جلسة Cloudflare منتهية")
            return []
        data = resp.json()
        content = data.get("content") or (data if isinstance(data, list) else [])
        if not content:
            st.info(f"ℹ️ Shifts API رجّع 0 شيفت لبكرة. (الرد: {str(data)[:200]})")
        return content
    except Exception as e:
        st.warning(f"⚠️ خطأ في تحليل الشيفتات: {e}")
        return []


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
shifts = get_tomorrow_shifts()

# معرفات الطيارين الذين لديهم شيفت غدًا
tomorrow_rider_ids = set()
for sh in shifts:
    # الـ ID ممكن يكون في employee_id أو employeeId أو جوه dict اسمه employee
    emp = (
        sh.get("employee_id")
        or sh.get("employeeId")
        or (sh.get("employee") or {}).get("id")
        or (sh.get("employee") or {}).get("employee_id")
    )
    try:
        tomorrow_rider_ids.add(int(emp))
    except (TypeError, ValueError):
        pass

st.caption(f"📅 شيفتات بكرة: {len(shifts)} | مناديب ليهم شيفت: {len(tomorrow_rider_ids)}")

# ==================== زر التحديث ====================
if st.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

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
    # المرور على كل الطيارين القادمين من الـ API مباشرة
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
live_map_tab, all_breaks_tab = st.tabs(["🗺️ Live Map", "☕ All Breaks"])

# ==================== تبويب الخريطة ====================
with live_map_tab:
    row1 = st.columns(4)
    with row1[0]:
        st.metric("Total Riders", total_riders)
    with row1[1]:
        st.metric("🟢 Working", working_count)
    with row1[2]:
        st.metric("🟡 Temp / Break", temp_offline_count + break_count)
    with row1[3]:
        st.metric("🔵 Starting", starting_count)

    row2 = st.columns(4)
    with row2[0]:
        st.metric("🔴 Late", late_count)
    with row2[1]:
        st.metric("📦 With Order", with_order_count)
    with row2[2]:
        st.metric("⚪ Without Order", without_order_count)
    with row2[3]:
        st.metric("☕ Break", break_count)

    filtered_riders = riders

    m = folium.Map(location=[31.2653, 32.3019], zoom_start=13)
    points = []

    for r in filtered_riders:
        # استخراج الإحداثيات من عدة أشكال محتملة
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

        # قراءة الـ ID من كل الحقول المحتملة (employee_id هو الأساسي)
        rider_id = (
            r.get("employee_id")
            or r.get("employeeId")
            or r.get("id")
        )
        try:
            has_shift_tomorrow = int(rider_id) in tomorrow_rider_ids
        except (TypeError, ValueError):
            has_shift_tomorrow = False

        # اسم المندوب (يظهر في الـ tooltip عند الوقوف بالماوس على الدبوس)
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
            <b>Completed Orders:</b> {r.get('completed_orders', 0)}<br>
            <b>Accepted Orders:</b> {r.get('accepted_orders', 0)}
        </div>
        """

        if status_info == "Working 🟢":
            # علامة خضراء على شكل قطرة مع نقطة ملونة حسب وجود أوردر نشط
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
            # علامة صفراء على شكل قطرة
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
            # علامة عادية حسب الحالة
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
# ==================== تبويب البريكات ====================
from urllib.parse import urlencode

# رابط فورم جوجل معبي بالبيانات (Prefilled Link)
FORM_BASE = "https://docs.google.com/forms/d/e/1FAIpQLSekxaUQPgiWq-Y_IPfsTx7wTANA324a2JklFJZ_Gxg9CGPaKA/viewform"

def make_break_url(rid):
    # يبني رابط الفورم معبي بالـ ID بتاع المندوب
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
                # زرار فك بريك — يفتح فورم جوجل معبي بالـ ID بتاع المندوب ده
                st.link_button("🔓 فك بريك", make_break_url(rid), use_container_width=True)
            st.divider()
    else:
        st.info("🟢 No riders are currently on break.")
