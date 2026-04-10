from playwright.sync_api import sync_playwright
import json
import os
import sys

# Fix Unicode encoding on Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

captured_data = []

def capture_api(response):
    try:
        # Capture XHR / fetch requests
        if response.request.resource_type in ["xhr", "fetch"]:
            print("\n📡 API URL:", response.url)

            try:
                data = response.json()

                print("📊 DATA:")
                print(json.dumps(data, indent=2))

                # ✅ Store clean structured data
                captured_data.append({
                    "url": response.url,
                    "data": data
                })

            except:
                print("⚠️ Not JSON response")

    except Exception as e:
        print("Error:", e)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Attach listener BEFORE navigation
        page.on("response", capture_api)

        print("Opening site...")
        page.goto("https://cloud.suryalog.com")

        print("Attempting automatic login...")
        # Wait for login form to appear
        page.wait_for_selector('#loginId', timeout=10000)
        
        # Fill in credentials
        page.fill('#loginId', "MAQ_Software")
        page.wait_for_timeout(500)
        page.fill('#password', "MAQ@1234")
        page.wait_for_timeout(500)
        
        # Click login button using the correct ID
        page.click('#btnlogin')
        print("Login button clicked, waiting for page to load...")
        
        # Wait for page to navigate after login
        page.wait_for_timeout(8000)

        print("Waiting for APIs...")
        page.wait_for_timeout(10000)

        print("Triggering interaction...")
        page.mouse.click(100, 100)
        page.wait_for_timeout(5000)

        print("Reloading...")
        page.reload()
        page.wait_for_timeout(10000)

        browser.close()
        
except Exception as e:
    print(f"\n⚠️ Browser automation error: {str(e)}")
    print("Continuing to save captured data if any...")

# ✅ Save all captured data into separate file
output_file = os.path.join(script_dir, "captured_api_data.json")

try:
    with open(output_file, "w") as f:
        json.dump(captured_data, f, indent=2)
    
    if captured_data:
        print(f"\n✅ Data saved successfully to {output_file}")
        print(f"   Total API calls captured: {len(captured_data)}")
    else:
        print(f"\n⚠️ No API data captured, but file created at {output_file}")
        print("   This may happen if login failed or APIs were not triggered")
        
except Exception as e:
    print(f"\n❌ Error saving data to {output_file}: {str(e)}")
    sys.exit(1)