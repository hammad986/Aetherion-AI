import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

options = Options()
options.add_argument('--headless')
options.add_argument('--disable-gpu')
options.add_argument('--log-level=3')

driver = webdriver.Chrome(options=options)
try:
    print("Loading http://localhost:5000 ...")
    driver.get("http://localhost:5000")
    time.sleep(2)  # wait for boot tasks
    
    print("\n--- BROWSER CONSOLE LOGS ---")
    logs = driver.get_log('browser')
    if not logs:
        print("No errors in console!")
    for log in logs:
        # filter out generic network errors (401/404) if needed
        print(f"[{log['level']}] {log['message']}")
        
    print("\n--- GLOBAL EXPORTS CHECK ---")
    functions_to_check = ['nxSetTab', 'nxRunOrStop', 'nxQueueTask', 'queueTask', 'nxDoLogin']
    for fn in functions_to_check:
        res = driver.execute_script(f"return typeof window.{fn};")
        print(f"window.{fn} = {res}")

finally:
    driver.quit()
