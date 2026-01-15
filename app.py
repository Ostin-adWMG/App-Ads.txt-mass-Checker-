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
""")

# ---------------- Input Tabs ----------------
tab1, tab2 = st.tabs(["📋 Вставить список", "📂 Загрузить файл"])

domains = []
with tab1:
    st.header("Список доменов")
    domain_input = st.text_area("Вставьте домены (один на строку, например: site.com)", height=200)
    if domain_input:
        domains = [d.strip() for d in domain_input.splitlines() if d.strip()]

with tab2:
    st.header("Загрузка файла")
    uploaded_file = st.file_uploader("Загрузите CSV или TXT файл", type=["csv", "txt"])
    if uploaded_file:
        stringio = StringIO(uploaded_file.getvalue().decode("utf-8"))
        uploaded_domains = [line.strip() for line in stringio.readlines() if line.strip()]
        domains.extend(uploaded_domains)

# Deduplicate
domains = list(dict.fromkeys(domains))
if domains:
    st.info(f"✅ Загружено уникальных доменов: {len(domains)}")

# ---------------- Settings Sidebar ----------------
st.sidebar.header("⚙️ Настройки")

timeout_sec = st.sidebar.slider("Тайм-аут запроса (сек)", 3, 20, 8)
max_threads = st.sidebar.slider("Количество потоков", 5, 50, 20)
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
        # Возвращаем netloc (домен) + path (если сайт в папке), но без лишних слешей
        return parsed.netloc + parsed.path.rstrip('/')
    except:
        return raw_url

def analyze_content(text):
    """Анализирует текст файла: считает строки, ищет HTML мусор."""
    if not text:
        return 0, "Empty File"
    
    # Проверка на Soft 404 (HTML вместо текста)
    text_lower = text.lower()[:500] # Смотрим только начало файла для скорости
    if "<!doctype html" in text_lower or "<html" in text_lower or "<body" in text_lower or "<div" in text_lower:
        return 0, "Soft 404 (HTML)"

    # Считаем реальные строки (не пустые, не комментарии)
    lines = text.splitlines()
    valid_count = 0
    for line in lines:
        line = line.strip()
        # Игнорируем пустые строки и комментарии
        if line and not line.startswith('#'):
            valid_count += 1
    
    if valid_count == 0:
        return 0, "Empty File"
    
    return valid_count, "Valid"

def check_domain(domain):
    """Основная функция проверки одного домена."""
    clean_d = clean_domain(domain)
    # Формируем целевой URL
    target_url = f"https://{clean_d}/app-ads.txt"
    # Если не сработает HTTPS, попробуем HTTP внутри сессии, но начнем с HTTPS
    
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    
    result = {
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
        # Попытка 2: Если SSL ошибка, пробуем без верификации
        try:
            response = session.get(target_url, timeout=timeout_sec, allow_redirects=True, verify=False)
        except Exception as e:
             # Попытка 3: Пробуем HTTP если совсем плохо
            try:
                target_url_http = f"http://{clean_d}/app-ads.txt"
                response = session.get(target_url_http, timeout=timeout_sec, allow_redirects=True)
                result["Final URL"] = response.url
            except Exception as e_final:
                result["Status"] = "Connection Error"
                result["Code"] = "ERR"
                return result
    except Exception as e:
        result["Status"] = "Connection Error"
        result["Code"] = "ERR"
        return result

    # Обработка ответа
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
    
    # Многопоточная обработка
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_domain = {executor.submit(check_domain, d): d for d in domains}
        
        for i, future in enumerate(as_completed(future_to_domain)):
            data = future.result()
            results_data.append(data)
            
            # Обновление прогресса
            percent = (i + 1) / len(domains)
            progress_bar.progress(percent)
            status_text.text(f"Обработано {i + 1} из {len(domains)}...")

    end_time = time.time()
    st.success(f"✅ Готово! Затрачено времени: {end_time - start_time:.2f} сек.")

    # ---------------- Display Results ----------------
    df = pd.DataFrame(results_data)
    
    # Сортировка: Сначала Valid, потом ошибки
    df = df.sort_values(by=["Lines"], ascending=False)
    
    # Визуальное оформление таблицы
    def highlight_status(val):
        color = 'black'
        if val == 'Valid':
            color = 'green'
        elif val == 'Not Found' or val == 'Empty File':
            color = 'red'
        elif 'Soft 404' in val:
            color = 'orange'
        return f'color: {color}; font-weight: bold'

    st.subheader("📊 Результаты проверки")
    st.dataframe(
        df.style.map(highlight_status, subset=['Status']),
        use_container_width=True,
        column_config={
            "Final URL": st.column_config.LinkColumn("Ссылка на файл"),
            "Lines": st.column_config.NumberColumn("Кол-во строк", format="%d")
        }
    )

    # ---------------- Export ----------------
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Скачать отчет (CSV)",
        data=csv,
        file_name='app_ads_report.csv',
        mime='text/csv',
    )
