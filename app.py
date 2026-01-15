import streamlit as st
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from io import StringIO
from urllib.parse import urlparse
import warnings

# Отключаем надоедливые предупреждения SSL (так как мы используем verify=False)
warnings.filterwarnings("ignore")

# ---------------- Page Setup ----------------
st.set_page_config(page_title="App-ads.txt Stealth Checker", layout="wide")
st.title("🥷 Stealth App-ads.txt Checker")
st.markdown("""
Улучшенная версия с **обходом защиты от ботов (403 Forbidden)**.
Имитирует реальный браузер с полными заголовками.
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

domains = list(dict.fromkeys(domains))

if domains:
    st.info(f"✅ Загружено уникальных доменов: {len(domains)}")

# ---------------- Settings Sidebar ----------------
st.sidebar.header("⚙️ Настройки")

# Увеличил дефолтный таймаут, так как некоторые сайты (Cloudflare) могут думать долго
timeout_sec = st.sidebar.slider("Тайм-аут (сек)", 5, 30, 10) 
max_threads = st.sidebar.slider("Количество потоков", 5, 50, 20)

# Маскировка: Заголовки реального Chrome
REAL_CHROME_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

# ---------------- Logic Functions ----------------

def clean_domain(raw_url):
    """Оставляет только чистый домен."""
    raw_url = raw_url.strip()
    if not raw_url.startswith("http"):
        raw_url = "http://" + raw_url
    try:
        parsed = urlparse(raw_url)
        return parsed.netloc + parsed.path.rstrip('/')
    except:
        return raw_url

def analyze_content(text):
    """Анализирует текст на Soft 404 и считает строки."""
    if not text:
        return 0, "Empty File"
    
    text_lower = text.lower()[:600]
    # Более строгая проверка на HTML
    if "<!doctype html" in text_lower or "<html" in text_lower or "<body" in text_lower:
        return 0, "Soft 404 (HTML)"
    
    # Проверка на JSON (иногда отдают JSON с ошибкой)
    if text.strip().startswith("{") and "error" in text_lower:
         return 0, "Soft 404 (JSON)"

    lines = text.splitlines()
    valid_count = 0
    for line in lines:
        line = line.strip()
        # Считаем строку валидной, если в ней есть запятая (формат app-ads) и нет #
        if line and not line.startswith('#'):
            valid_count += 1
    
    if valid_count == 0:
        return 0, "Empty File"
    
    return valid_count, "Valid"

def check_domain(domain, index):
    clean_d = clean_domain(domain)
    target_url = f"https://{clean_d}/app-ads.txt"
    
    session = requests.Session()
    # ПРИМЕНЯЕМ МАСКИРОВКУ
    session.headers.update(REAL_CHROME_HEADERS)
    
    result = {
        "Index": index,
        "Input Domain": domain,
        "Final URL": target_url,
        "Status": "Unknown",
        "Code": 0,
        "Lines": 0
    }

    try:
        # Попытка 1: Обычный HTTPS запрос
        response = session.get(target_url, timeout=timeout_sec, allow_redirects=True)
        
        # Если получили 403 Forbidden, пробуем трюк: сбрасываем сессию или меняем протокол
        if response.status_code == 403 or response.status_code == 429:
             time.sleep(1) # Небольшая пауза
             # Пробуем HTTP (иногда https блокируют жестче)
             target_url_http = f"http://{clean_d}/app-ads.txt"
             response = session.get(target_url_http, timeout=timeout_sec, allow_redirects=True, verify=False)
             result["Final URL"] = response.url

    except requests.exceptions.SSLError:
        # Попытка 2: SSL Error -> пробуем без верификации
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
    except Exception as e:
        # Ловим Connection Error (например, 504 Gateway Timeout)
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
        result["Status"] = "Forbidden (Bot Block)"
    elif response.status_code == 404:
        result["Status"] = "Not Found"
    elif response.status_code == 522 or response.status_code == 504:
         result["Status"] = "Server Timeout (Cloudflare)"
    else:
        result["Status"] = f"HTTP {response.status_code}"

    return result

# ---------------- Main Execution ----------------

if st.button("🚀 Начать проверку", disabled=not domains):
    start_time = time.time()
    results_data = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_domain = {executor.submit(check_domain, d, i): d for i, d in enumerate(domains)}
        
        for i, future in enumerate(as_completed(future_to_domain)):
            data = future.result()
            results_data.append(data)
            
            percent = (i + 1) / len(domains)
            progress_bar.progress(percent)
            status_text.text(f"Обработано {i + 1} из {len(domains)}...")

    end_time = time.time()
    st.success(f"✅ Готово! Затрачено времени: {end_time - start_time:.2f} сек.")

    df = pd.DataFrame(results_data)
    
    if not df.empty:
        df = df.sort_values(by=["Index"], ascending=True)
        df_display = df.drop(columns=["Index"]) 
    else:
        df_display = df

    def highlight_status(val):
        color = 'black'
        if val == 'Valid':
            color = 'green'
        elif 'Not Found' in val or 'Empty' in val or 'Error' in val:
            color = 'red'
        elif 'Forbidden' in val or 'Soft 404' in val or 'Timeout' in val:
            color = 'orange'
        return f'color: {color}; font-weight: bold'

    st.subheader("📊 Результаты проверки")
    st.dataframe(
        df_display.style.map(highlight_status, subset=['Status']),
        use_container_width=True,
        column_config={
            "Final URL": st.column_config.LinkColumn("Ссылка"),
            "Lines": st.column_config.NumberColumn("Строк", format="%d")
        }
    )

    csv = df_display.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Скачать CSV",
        data=csv,
        file_name='app_ads_stealth_report.csv',
        mime='text/csv',
    )
