import unittest

from paicli_py.browser import BrowserMode, BrowserService, BrowserSession, ProbeResult, SensitivePagePolicy, handle_browser_command


class FakeConnectivity:
    def __init__(self, ok=True, tabs=None):
        self.ok = ok
        self.tabs_payload = tabs or []
        self.probed = []

    def probe(self, port=9222):
        self.probed.append(port)
        if self.ok:
            return ProbeResult(True, f"http://127.0.0.1:{port}")
        return ProbeResult(False, message="no chrome")

    def list_tabs(self, browser_url):
        return self.tabs_payload


class BrowserTest(unittest.TestCase):
    def test_connect_status_tabs_and_disconnect(self):
        service = BrowserService(connectivity=FakeConnectivity(tabs=[
            {"id": "page_1", "title": "Home", "url": "https://example.com"}
        ]))

        self.assertIn("browser connected", service.connect(9223))
        self.assertEqual(BrowserMode.SHARED, service.session.mode)
        self.assertIn("Browser tabs: 1", service.tabs())
        self.assertIn("page_1", service.tabs())
        self.assertIn("Browser mode: shared", service.status())
        self.assertIn("browser disconnected", service.disconnect())
        self.assertEqual(BrowserMode.ISOLATED, service.session.mode)

    def test_browser_command_handler(self):
        service = BrowserService(connectivity=FakeConnectivity())

        self.assertIn("Browser mode", handle_browser_command(service, "status"))
        self.assertIn("browser connected", handle_browser_command(service, "connect 9224"))
        self.assertIn("Usage", handle_browser_command(service, "connect nope"))
        self.assertIn("browser disconnected", handle_browser_command(service, "disconnect"))

    def test_sensitive_policy_matches_defaults_and_user_patterns(self):
        policy = SensitivePagePolicy(patterns=["*://example.test/private/*"])

        self.assertTrue(policy.is_sensitive("https://example.test/private/a"))
        self.assertFalse(policy.is_sensitive("https://example.test/public/a"))

    def test_browser_guard_requires_approval_for_sensitive_write(self):
        service = BrowserService(policy=SensitivePagePolicy(patterns=["*://example.test/private/*"]))
        service.session.switch_to_shared("http://127.0.0.1:9222")
        service.session.remember_navigation("https://example.test/private/settings")

        result = service.check_tool("mcp__chrome-devtools__click", {"uid": "button"}, mutate_session=False)

        self.assertTrue(result.requires_approval)
        self.assertTrue(result.sensitive)

    def test_browser_guard_blocks_closing_unowned_shared_tabs(self):
        session = BrowserSession()
        session.switch_to_shared("http://127.0.0.1:9222")
        service = BrowserService(session=session)

        blocked = service.check_tool("mcp__chrome-devtools__close_page", {"pageId": "page_99"}, mutate_session=False)
        session.record_opened_tab("page_99")
        allowed = service.check_tool("mcp__chrome-devtools__close_page", {"pageId": "page_99"}, mutate_session=False)

        self.assertTrue(blocked.blocked)
        self.assertFalse(allowed.blocked)

    def test_navigation_mutates_session_url(self):
        service = BrowserService()

        result = service.check_tool("mcp__chrome-devtools__navigate_page", {"url": "https://example.com"})

        self.assertFalse(result.blocked)
        self.assertEqual("https://example.com", service.session.last_navigated_url)


if __name__ == "__main__":
    unittest.main()
