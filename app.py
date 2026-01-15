import streamlit as st
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from io import StringIO
import re
from urllib.parse import urlparse

# ---------------- Page Setup ----------------
st.set_page_config(page_title="App-ads.txt Health Checker", layout="wide")
st.title("🛡️ App-ads.txt Health & Line Counter")
st.markdown("""
Этот инструмент проверяет наличие файла **app-ads.txt**, обрабатывает редиректы, 
игнорирует HTML-страницы ошибок (Soft 404) и считает количество валидных строк.
**Результаты выводятся в том же порядке, что и в вашем списке.**
""")

# ---------------- Input Tabs ----------------
tab1, tab2 = st.tabs(["📋 Вставить список", "📂 Загрузить файл"])

domains = []
with tab1:
    st.header("Список доменов")
    domain_input = st.text_area("Вставьте домены (один на строку)", height=200)
    if domain_input:
        domains = [d.strip() for d in domain_input.splitlines() if d.strip()]

with tab2:
    st.header("Загрузка файла")
    uploaded_file = st.file_uploader("Загрузите CSV или TXT файл", type=["csv", "txt"])
    if uploaded_file:
        stringio = StringIO(uploaded_file.getvalue().decode("utf-8"))
        uploaded_domains = [line.strip() for line in stringio.readlines() if line.strip()]
        domains.extend(uploaded_domains)

# Deduplicate preserving order (Python 3.7+ dict preserves insertion order)
domains = list(dict.fromkeys(domains))

if domains:
    st.info(f"✅ Загружено уникальных доменов: {len(domains)}")

# ---------------- Settings Sidebar ----------------
st.sidebar.header("⚙️ Настройки")

timeout_sec = st.sidebar.slider("Тайм-аут запроса (сек)", 3, 20, 5)
max_threads = st.sidebar.slider("Количество потоков", 5, 50, 30)
ua_mode = st.sidebar.radio("Режим User-Agent", ["Chrome (Windows)", "Google Bot"])

if ua_mode == "Chrome (Windows)":
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
else:
    USER_AGENT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# ---------------- Logic Functions ----------------

def clean_domain(raw_url):
    """Очищает ввод, оставляя только домен или базовый URL без хвостов."""
    raw_url = raw_url.strip()
    if not raw_url.startswith("http"):
        raw_url = "http://" + raw_url
    try:
        parsed = urlparse(raw_url)
        return parsed.netloc + parsed.path.rstrip('/')
    except:
        return raw_url

def analyze_content(text):
    """Анализирует текст файла."""
    if not text:
        return 0, "Empty File"
    
    text_lower = text.lower()[:500] 
    if "<!doctype html" in text_lower or "<html" in text_lower or "<body" in text_lower or "<div" in text_lower:
        return 0, "Soft 404 (HTML)"

    lines = text.splitlines()
    valid_count = 0
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            valid_count += 1
    
    if valid_count == 0:
        return 0, "Empty File"
    
    return valid_count, "Valid"

def check_domain(domain, index):
    """
    Проверка домена.
    Аргумент index нужен, чтобы запомнить порядковый номер.
    """
    clean_d = clean_domain(domain)
    target_url = f"https://{clean_d}/app-ads.txt"
    
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    
    result = {
        "Index": index, # Сохраняем исходный номер строки
        "Input Domain": domain,
        "Final URL": target_url,
        "Status": "Unknown",
        "Code": 0,
        "Lines": 0
    }

    try:
        # Попытка 1: HTTPS
        response = session.get(target_url, timeout=timeout_sec, allow_redirects=True)
    except requests.exceptions.SSLError:
        # Попытка 2: HTTPS без проверки сертификата
        try:
            response = session.get(target_url, timeout=timeout_sec, allow_redirects=True, verify=False)
        except Exception:
             # Попытка 3: HTTP
            try:
                target_url_http = f"http://{clean_d}/app-ads.txt"
                response = session.get(target_url_http, timeout=timeout_sec, allow_redirects=True)
                result["Final URL"] = response.url
            except Exception:
                result["Status"] = "Connection Error"
                result["Code"] = "ERR"
                return result
    except Exception:
        result["Status"] = "Connection Error"
        result["Code"] = "ERR"
        return result

    result["Code"] = response.status_code
    result["Final URL"] = response.url

    if response.status_code == 200:
        count, status_msg = analyze_content(response.text)
        result["Lines"] = count
        result["Status"] = status_msg
    elif response.status_code == 403:
        result["Status"] = "Forbidden"
    elif response.status_code == 404:
        result["Status"] = "Not Found"
    else:
        result["Status"] = f"HTTP {response.status_code}"

    return result

# ---------------- Main Execution ----------------

if st.button("🚀 Начать проверку", disabled=not domains):
    start_time = time.time()
    results_data = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Запускаем потоки, передавая индекс (i)
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Передаем enumerate(domains), чтобы у каждого домена был свой номер (0, 1, 2...)
        future_to_domain = {executor.submit(check_domain, d, i): d for i, d in enumerate(domains)}
        
        for i, future in enumerate(as_completed(future_to_domain)):
            data = future.result()
            results_data.append(data)
            
            percent = (i + 1) / len(domains)
            progress_bar.progress(percent)
            status_text.text(f"Обработано {i + 1} из {len(domains)}...")

    end_time = time.time()
    st.success(f"✅ Готово! Затрачено времени: {end_time - start_time:.2f} сек.")

    # ---------------- Display Results ----------------
    df = pd.DataFrame(results_data)
    
    # === ВАЖНО: Сортируем обратно по индексу ===
    if not df.empty:
        df = df.sort_values(by=["Index"], ascending=True)
        # Удаляем колонку Index, чтобы она не мешалась в таблице (опционально, но так красивее)
        df_display = df.drop(columns=["Index"]) 
    else:
        df_display = df

    def highlight_status(val):
        color = 'black'
        if val == 'Valid':
            color = 'green'
        elif val == 'Not Found' or val == 'Empty File' or val == 'Connection Error':
            color = 'red'
        elif 'Soft 404' in val or 'Forbidden' in val:
            color = 'orange'
        return f'color: {color}; font-weight: bold'

    st.subheader("📊 Результаты проверки")
    st.dataframe(
        df_display.style.map(highlight_status, subset=['Status']),
        use_container_width=True,
        column_config={
            "Final URL": st.column_config.LinkColumn("Ссылка на файл"),
            "Lines": st.column_config.NumberColumn("Кол-во строк", format="%d")
        }
    )

    # ---------------- Export ----------------
    # Для CSV берем отсортированный фрейм, но без колонки Index
    csv = df_display.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Скачать отчет (CSV)",
        data=csv,
        file_name='app_ads_report.csv',
        mime='text/csv',
    )
