from jarvis import JarvisBrain, LocalController, SandboxedVM, get_web_elements


def test_brain_builds_plan_from_task():
    brain = JarvisBrain()
    plan = brain.plan("Open the recipe site and summarize the ingredients for pasta.")

    assert plan["objective"] == "Open the recipe site and summarize the ingredients for pasta."
    assert plan["tool_priority"] == ["get_web_elements", "get_ui_elements", "read_screen"]
    assert "browser" in plan["tools"]
    assert "read_screen" in plan["tools"]
    assert "get_web_elements" in plan["tools"]
    assert len(plan["steps"]) >= 2


def test_get_web_elements_returns_structured_context():
    result = get_web_elements()

    assert "elements" in result
    assert result["elements"][0]["id"] == "apply_button"
    assert result["elements"][0]["type"] == "button"
    assert result["elements"][1]["id"] == "email"


def test_controller_selects_and_runs_actions():
    brain = JarvisBrain()
    plan = brain.plan("Check the login page and confirm the form exists.")
    controller = LocalController(brain)

    result = controller.execute(plan)

    assert result["status"] == "ok"
    assert result["plan"]["objective"].startswith("Check the login page")
    assert result["actions"]


def test_sandbox_tracks_capabilities_and_execution_state():
    sandbox = SandboxedVM()
    sandbox.open_browser("https://example.com")

    assert sandbox.browser_active is True
    assert sandbox.current_url == "https://example.com"
    assert "browser" in sandbox.capabilities
    assert "get_web_elements" in sandbox.capabilities
    assert "get_ui_elements" in sandbox.capabilities
    assert "read_screen" in sandbox.capabilities

    sandbox.close_browser()
    assert sandbox.browser_active is False
