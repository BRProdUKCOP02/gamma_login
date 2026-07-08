import os
import asyncio
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()


def get_gamma_settings():
    """
    Resolve environment-specific Gamma settings.
    Priority:
      1) GAMMA_<ENV>_LOGIN_URL / USERNAME / PASSWORD
      2) Legacy fallback: GAMMA_LOGIN_URL / GAMMA_USERNAME / GAMMA_PASSWORD
    """
    env = os.getenv("GAMMA_ENV", "uat").strip().lower()
    if env not in ("uat", "live"):
        raise ValueError("GAMMA_ENV must be 'uat' or 'live'")

    prefix = f"GAMMA_{env.upper()}"

    login_url = os.getenv(f"{prefix}_LOGIN_URL", "").strip() or os.getenv("GAMMA_LOGIN_URL", "").strip()
    username = os.getenv(f"{prefix}_USERNAME", "").strip() or os.getenv("GAMMA_USERNAME", "").strip()
    password = os.getenv(f"{prefix}_PASSWORD", "").strip() or os.getenv("GAMMA_PASSWORD", "").strip()

    missing = []
    if not login_url:
        missing.append(f"{prefix}_LOGIN_URL (or GAMMA_LOGIN_URL)")
    if not username:
        missing.append(f"{prefix}_USERNAME (or GAMMA_USERNAME)")
    if not password:
        missing.append(f"{prefix}_PASSWORD (or GAMMA_PASSWORD)")

    if missing:
        raise ValueError(f"Missing required env vars for {env.upper()}: {', '.join(missing)}")

    return env, login_url, username, password


class GammaLogin:
    DEBUG_DIR = "debug_screens"

    def __init__(self, username: str, password: str, login_url: str):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.browser = None
        self.playwright = None

        os.makedirs(self.DEBUG_DIR, exist_ok=True)

        # Safe logging setup (avoid duplicate handlers across imports)
        self.logger = logging.getLogger("gamma_login")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler("gamma_login.log")
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)

    def ts_path(self, filename: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.DEBUG_DIR, f"{ts}_{filename}")

    async def debug_page_elements(self, page):
        """Debug helper to inspect page elements"""
        try:
            await page.screenshot(path=self.ts_path("debug_page_elements.png"))

            title = await page.title()
            self.logger.info(f"Page title: {title}")
            self.logger.info(f"Current URL: {page.url}")

            buttons = await page.query_selector_all("button")
            self.logger.info(f"Found {len(buttons)} buttons on page:")
            for i, button in enumerate(buttons):
                try:
                    text = await button.inner_text()
                    btn_type = await button.get_attribute("type")
                    btn_id = await button.get_attribute("id")
                    btn_name = await button.get_attribute("name")
                    btn_class = await button.get_attribute("class")
                    btn_value = await button.get_attribute("value")
                    is_visible = await button.is_visible()
                    is_enabled = await button.is_enabled()
                    self.logger.info(
                        f"  Button {i}: text='{text}', type='{btn_type}', id='{btn_id}', "
                        f"name='{btn_name}', class='{btn_class}', value='{btn_value}', "
                        f"visible={is_visible}, enabled={is_enabled}"
                    )
                except Exception as e:
                    self.logger.error(f"  Button {i}: Error getting attributes - {e}")

            inputs = await page.query_selector_all("input")
            self.logger.info(f"Found {len(inputs)} input elements:")
            for i, inp in enumerate(inputs):
                try:
                    inp_type = await inp.get_attribute("type")
                    inp_id = await inp.get_attribute("id")
                    inp_name = await inp.get_attribute("name")
                    inp_value = await inp.get_attribute("value")
                    is_visible = await inp.is_visible()
                    is_enabled = await inp.is_enabled()
                    self.logger.info(
                        f"  Input {i}: type='{inp_type}', id='{inp_id}', name='{inp_name}', "
                        f"value='{inp_value}', visible={is_visible}, enabled={is_enabled}"
                    )
                except Exception as e:
                    self.logger.error(f"  Input {i}: Error getting attributes - {e}")

        except Exception as e:
            self.logger.error(f"Error during page debugging: {e}")

    async def wait_for_login_form(self, page):
        """Wait for the login form to be ready"""
        try:
            await page.wait_for_selector("input#username", timeout=15000)
            await page.wait_for_selector("input#password", timeout=5000)
            await asyncio.sleep(2)

            username_field = await page.query_selector("input#username")
            password_field = await page.query_selector("input#password")

            if username_field and password_field:
                username_visible = await username_field.is_visible()
                password_visible = await password_field.is_visible()

                if username_visible and password_visible:
                    self.logger.info("Login form is ready")
                    return True

            self.logger.error("Login form fields not properly visible")
            return False
        except Exception as e:
            self.logger.error(f"Error while waiting for login form: {e}")
            return False

    async def handle_log_in_again_button(self, page):
        """Handle the 'Log In Again' secondary authentication step"""
        try:
            self.logger.info("Checking for 'Log In Again' button...")
            await asyncio.sleep(2)

            login_again_selectors = [
                'span.label:has-text("Log In Again")',
                '*:has-text("Log In Again")',
                'button:has-text("Log In Again")',
                'a:has-text("Log In Again")',
                'span:has-text("Log In Again")',
                '[class*="label"]:has-text("Log In Again")',
                'span.label:has-text("Log In Again") ~ *',
                'span.label:has-text("Log In Again")',
            ]

            parent_selectors = [
                'button:has(span.label:has-text("Log In Again"))',
                'a:has(span.label:has-text("Log In Again"))',
                'div:has(span.label:has-text("Log In Again"))',
                '*[role="button"]:has(span.label:has-text("Log In Again"))',
            ]

            all_selectors = login_again_selectors + parent_selectors

            for selector in all_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            try:
                                element_text = await element.inner_text()
                                tag_name = await element.evaluate("el => el.tagName")
                                self.logger.info(f"Found 'Log In Again' element: {tag_name} with text: '{element_text}'")
                            except Exception:
                                self.logger.info(f"Found 'Log In Again' element with selector: {selector}")

                            await page.screenshot(path=self.ts_path("before_login_again_click.png"))

                            click_methods = [
                                ("normal_click", lambda: element.click(timeout=5000)),
                                ("force_click", lambda: element.click(force=True, timeout=5000)),
                                ("js_click", lambda: page.evaluate("element => element.click()", element)),
                                ("dispatch_click", lambda: element.dispatch_event("click")),
                            ]

                            for method_name, click_method in click_methods:
                                try:
                                    await click_method()
                                    self.logger.info(f"✅ Successfully clicked 'Log In Again' using {method_name}")
                                    await asyncio.sleep(3)
                                    await page.screenshot(path=self.ts_path("after_login_again_click.png"))
                                    return True
                                except Exception as click_error:
                                    self.logger.warning(f"Click method {method_name} failed: {click_error}")
                                    continue

                            self.logger.warning(f"All click methods failed for element found with: {selector}")

                except Exception as e:
                    self.logger.debug(f"Selector '{selector}' failed: {e}")
                    continue

            self.logger.info("No 'Log In Again' button found - proceeding normally")
            return True

        except PlaywrightTimeoutError:
            self.logger.error("Login form not found within timeout")
            return False
        except Exception as e:
            self.logger.error(f"Error handling 'Log In Again' button: {e}")
            return True

    async def find_and_click_login_button(self, page):
        """Systematically find and click the login button"""
        await self.debug_page_elements(page)

        login_selectors = [
            'button[name="login"]',
            'input[name="login"]',
            "button#kc-login",
            "input#kc-login",
            'form button[type="submit"]',
            'form input[type="submit"]',
            'button:has-text("Sign In")',
            'button:has-text("Log In")',
            'button:has-text("Login")',
            'input[value*="Sign"]:not([value*="Sign up"])',
            'input[value*="Log"]:not([value*="Log out"])',
            'button.btn-primary:not(:has-text("Accept"))',
            "button.login-btn",
            "button.submit-btn",
            'form button:not(:has-text("Accept")):not(:has-text("Cookie")):not(:has-text("Consent"))',
        ]

        self.logger.info("Starting systematic login button search...")

        for i, selector in enumerate(login_selectors):
            try:
                self.logger.info(f"Trying selector {i + 1}/{len(login_selectors)}: {selector}")

                element = await page.query_selector(selector)
                if not element:
                    self.logger.info(f"  No element found for: {selector}")
                    continue

                text = await element.inner_text()
                tag_name = await element.evaluate("el => el.tagName")
                is_visible = await element.is_visible()
                is_enabled = await element.is_enabled()
                element_type = await element.get_attribute("type")
                element_name = await element.get_attribute("name")
                element_id = await element.get_attribute("id")

                self.logger.info(
                    f"  Found {tag_name}: text='{text}', type='{element_type}', "
                    f"name='{element_name}', id='{element_id}'"
                )
                self.logger.info(f"  Visible: {is_visible}, Enabled: {is_enabled}")

                if not is_visible or not is_enabled:
                    self.logger.info("  Skipping - not clickable")
                    continue

                text_lower = (text or "").lower()
                if any(word in text_lower for word in ["accept", "cookie", "consent", "agree"]):
                    self.logger.info(f"  Skipping cookie/consent button: '{text}'")
                    continue

                self.logger.info(f"  Attempting to click: '{text}'")

                try:
                    await page.screenshot(path=self.ts_path(f"before_login_click_{i}.png"))
                    await element.click(timeout=5000)
                    self.logger.info(f"✅ Successfully clicked login button: '{text}' using selector: {selector}")
                    await page.screenshot(path=self.ts_path(f"after_login_click_{i}.png"))
                    return True
                except Exception as click_error:
                    # Strip out the massive Playwright call log
                    clean_err = str(click_error).split("Call log:")[0].strip()
                    self.logger.warning(f"  Click failed: {clean_err}")

                    try:
                        await element.click(force=True)
                        self.logger.info(f"✅ Force click succeeded on: '{text}'")
                        return True
                    except Exception:
                        pass

                    try:
                        await page.evaluate("element => element.click()", element)
                        self.logger.info(f"✅ JS click succeeded on: '{text}'")
                        return True
                    except Exception:
                        pass

            except Exception as e:
                self.logger.error(f"Error processing selector '{selector}': {e}")
                continue

        self.logger.error("❌ No login button could be clicked")
        self.logger.info("Trying last resort methods...")

        last_resort_methods = [
            ("Press Enter on password field", lambda: page.press("input#password", "Enter")),
            ("Press Enter on username field", lambda: page.press("input#username", "Enter")),
            ("Submit form via JavaScript", lambda: page.evaluate('document.querySelector("form").submit()')),
        ]

        for method_name, method_func in last_resort_methods:
            try:
                self.logger.info(f"Trying: {method_name}")
                await method_func()
                self.logger.info(f"✅ {method_name} executed successfully")
                return True
            except Exception as e:
                self.logger.error(f"❌ {method_name} failed: {e}")

        return False

    async def login(self):
        page = None
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
            context = await self.browser.new_context()
            page = await context.new_page()

            self.logger.info(f"Navigating to login URL: {self.login_url}")
            await page.goto(self.login_url, wait_until="networkidle", timeout=30000)
            await page.screenshot(path=self.ts_path("01_page_loaded.png"))

            if not await self.wait_for_login_form(page):
                self.logger.error("Login form not ready")
                return None

            await page.screenshot(path=self.ts_path("02_before_filling.png"))

            await page.fill("input#username", self.username)
            self.logger.info("✅ Filled username field")

            await page.fill("input#password", self.password)
            self.logger.info("✅ Filled password field")

            await page.screenshot(path=self.ts_path("03_after_filling.png"))

            self.logger.info("Checking for cookie banner...")
            try:
                await asyncio.sleep(1)

                cookie_selectors = [
                    'button:has-text("Accept")',
                    'button:has-text("Accept all")',
                    'button:has-text("I accept")',
                    'button:has-text("Agree")',
                    'button[id*="accept"]',
                    'button[class*="accept"]',
                ]

                for cookie_sel in cookie_selectors:
                    try:
                        cookie_button = await page.query_selector(cookie_sel)
                        if cookie_button and await cookie_button.is_visible():
                            button_text = await cookie_button.inner_text()
                            self.logger.info(f"Found cookie button: '{button_text}'")
                            await cookie_button.click()
                            self.logger.info("✅ Clicked cookie acceptance button")
                            await asyncio.sleep(1)
                            await page.screenshot(path=self.ts_path("03b_after_cookie_accept.png"))
                            break
                    except Exception as e:
                        self.logger.debug(f"Cookie selector '{cookie_sel}' failed: {e}")
                        continue
                else:
                    self.logger.info("No cookie banner found")

            except Exception as e:
                self.logger.info(f"Cookie banner handling completed with note: {e}")

            self.logger.info("Now attempting to click login button...")

            if await self.find_and_click_login_button(page):
                self.logger.info("✅ Login button clicked successfully")

                try:
                    base_url = self.login_url.split("?")[0]
                    await page.wait_for_url(lambda url: not url.startswith(base_url), timeout=20000)
                    self.logger.info(f"✅ Redirected to: {page.url}")
                    await page.screenshot(path=self.ts_path("07_after_redirect.png"))

                    if await self.handle_log_in_again_button(page):
                        self.logger.info("✅ Handled 'Log In Again' step")
                        await page.screenshot(path=self.ts_path("08_after_login_again.png"))

                    return page

                except PlaywrightTimeoutError:
                    self.logger.warning("No URL change detected - checking for errors...")
                    # --- NEW FIX: Check for success text even if URL didn't change ---
                    try:
                        # Scan page for success indicators like "Logout" or "Welcome"
                        content = await page.content()
                        if "Logout" in content or "Welcome" in content:
                            self.logger.info("✅ Login confirmed! (Found 'Logout/Welcome' text despite URL warning)")
                            return page
                    except Exception:
                        pass
                    # -----------------------------------------------------------------
                    await page.screenshot(path=self.ts_path("08_checking_for_errors.png"))

                    error_selectors = [
                        ".alert-error",
                        ".kc-feedback-text",
                        ".error",
                        ".invalid-feedback",
                        '*:has-text("Invalid")',
                        '*:has-text("Error")',
                        '*:has-text("incorrect")',
                        '*:has-text("failed")',
                    ]

                    for error_sel in error_selectors:
                        try:
                            error_element = await page.query_selector(error_sel)
                            if error_element and await error_element.is_visible():
                                error_text = await error_element.inner_text()
                                self.logger.error(f"❌ Login error found: {error_text}")
                                return None
                        except Exception:
                            continue

                    if await self.handle_log_in_again_button(page):
                        self.logger.info("✅ Handled 'Log In Again' step after timeout")
                        await page.screenshot(path=self.ts_path("09_after_login_again_timeout.png"))

                    self.logger.info("No errors found - login might be successful")
                    return page

            else:
                self.logger.error("❌ Failed to click login button")
                return None

        except Exception as e:
            self.logger.exception(f"Unexpected error during login: {e}")
            if page:
                await page.screenshot(path=self.ts_path("login_exception.png"))

        return None

    async def cleanup(self):
        """Clean up browser and playwright resources"""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as cleanup_err:
            self.logger.error(f"Error during cleanup: {cleanup_err}")

    async def attempt_login_with_retries(self, retries: int = 3, delay: int = 5):
        for attempt in range(1, retries + 1):
            self.logger.info(f"🔄 Login attempt {attempt}/{retries}")
            page = await self.login()
            if page:
                self.logger.info(f"✅ Login successful on attempt {attempt}")
                return page

            await self.cleanup()

            if attempt < retries:
                wait_time = delay * attempt
                self.logger.info(f"⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        await self.cleanup()
        self.send_teams_alert("🚨 *Gamma Login Failure*: All retry attempts failed.")
        self.logger.error("❌ All login attempts failed")
        return None

    def send_teams_alert(self, message: str):
        webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
        if not webhook_url:
            self.logger.error("TEAMS_WEBHOOK_URL not set")
            return

        payload = {"text": message}
        try:
            r = requests.post(webhook_url, json=payload, timeout=15)
            if r.status_code == 200:
                self.logger.info("Teams alert sent")
            else:
                self.logger.error(f"Teams alert failed: {r.status_code} {r.text}")
        except Exception as e:
            self.logger.exception(f"Error sending Teams alert: {e}")


# Entry point
def main():
    try:
        env, login_url, user, pw = get_gamma_settings()
        print(f"Using GAMMA_ENV={env}")
    except Exception as e:
        print(f"❌ {e}")
        return False

    async def run_login():
        login_instance = GammaLogin(user, pw, login_url)
        try:
            page = await login_instance.attempt_login_with_retries()
            if page:
                print("✅ Logged in successfully. Browser remains open for further steps.")
                input("Press Enter to close browser and exit...")
                return True
            else:
                print("❌ Login failed after retries.")
                return False
        finally:
            await login_instance.cleanup()

    success = asyncio.run(run_login())
    return success


if __name__ == "__main__":
    main()
