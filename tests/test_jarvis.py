import json

from jarvis import JarvisAgent, JarvisBrain, LocalController, MemoryManager, SandboxVM, SandboxedVM, get_web_elements
from server import (
    DEFAULT_GEMINI_MODELS,
    answer_google_doc_question,
    build_dom_paste_plan,
    build_google_auth_url,
    build_memory_context,
    classify_browser_route,
    extract_gemini_text,
    get_gemini_model,
    get_gmail_status,
    infer_browser_target,
    parse_doc_paste_task,
    parse_email_task,
    read_google_doc_summary,
    send_gmail_message,
    strip_doc_context,
)


def test_link_elements_do_not_get_fill_targets():
    sandbox = SandboxedVM()
    sandbox.open_browser("data:text/html,<html><body><a href='https://example.com'>Open link</a><input id='search' type='search' /></body></html>")

    state = sandbox.get_state()
    link_ids = [element["id"] for element in state["elements"] if element["type"] == "a"]
    assert not any(element_id == "a_1" for element_id in link_ids)
    assert any(element["type"] == "input" for element in state["elements"])

    sandbox.close_browser()


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


def test_browser_agent_behaves_human_like_when_browsing_and_interacting():
    sandbox = SandboxedVM()
    sandbox.open_browser(
        "data:text/html,<html><body><input id='search' placeholder='Search recipes' /><button id='submit'>Search</button><p>Find dinner ideas</p></body></html>"
    )
    agent = JarvisAgent(sandbox=sandbox)

    result = agent.run("Search for pasta recipes and click Search", url=None)

    assert result["status"] == "ok"
    assert result["interaction_style"] == "human_like"
    assert "pasta" in result["query"]
    assert result["action_log"]
    assert any(action["action"] == "fill" for action in result["action_log"])
    assert any(action["action"] == "click" for action in result["action_log"])

    sandbox.close_browser()


def test_browser_agent_handles_multistep_browsing_sequence():
    sandbox = SandboxedVM()
    sandbox.open_browser(
        "data:text/html,<html><body><input id='search' placeholder='Search recipes' /><button id='submit'>Search</button><div id='results'>Pasta results</div></body></html>"
    )
    agent = JarvisAgent(sandbox=sandbox)

    result = agent.run("Open recipe site, search for pasta recipes, click Search, and inspect the results", url=None)

    assert result["status"] == "ok"
    assert result["interaction_style"] == "human_like"
    assert result["step_count"] >= 4
    assert len(result["steps"]) >= 4
    assert any(step["kind"] == "open" for step in result["steps"])
    assert any(step["kind"] == "fill" for step in result["steps"])
    assert any(step["kind"] == "click" for step in result["steps"])
    assert any(step["kind"] == "observe" for step in result["steps"])

    sandbox.close_browser()


def test_browser_agent_avoids_generated_button_ids_when_clicking_visible_controls():
    sandbox = SandboxedVM()
    sandbox.open_browser(
        "data:text/html,<html><body><input id='search' placeholder='Search recipes' /><button>Search</button></body></html>"
    )
    agent = JarvisAgent(sandbox=sandbox)

    result = agent.run("Search for pasta recipes", url=None)

    click_targets = [entry["target"] for entry in result["action_log"] if entry.get("action") == "click"]
    assert click_targets
    assert all("button_" not in str(target) for target in click_targets)

    sandbox.close_browser()


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


def test_browser_route_is_decided_by_gemini_when_web_tasks_are_requested(monkeypatch):
    monkeypatch.setattr("server.call_gemini", lambda *args, **kwargs: '{"route": "browser"}')
    decision = classify_browser_route("Google the definition of an apple")

    assert decision["route"] == "browser"


def test_browser_route_detects_email_and_site_tasks_without_hinting_at_chat(monkeypatch):
    monkeypatch.setattr("server.call_gemini", lambda *args, **kwargs: '{"route": "chat"}')

    gmail_decision = classify_browser_route("log into gmail and email foo@example.com with a random sentence")
    site_decision = classify_browser_route("go to wikipedia and summarize Heron of Alexandria's pressure pump")

    assert gmail_decision["route"] == "browser"
    assert site_decision["route"] == "browser"


def test_sandbox_vm_alias_is_defined_and_matches_runtime():
    assert SandboxVM is SandboxedVM
    assert SandboxVM().capabilities["browser"] is True


def test_playwright_vm_exposes_state_and_allows_gemini_controlled_actions():
    sandbox = SandboxedVM()
    sandbox.open_browser("data:text/html,<html><body><input id='email' /><button id='submit'>Submit</button></body></html>")

    state = sandbox.get_state()
    assert state["url"].startswith("data:text/html")
    assert any(element["id"] == "email" for element in state["elements"])
    assert any(element["id"] == "submit" for element in state["elements"])

    result = sandbox.execute_action({"action": "fill", "target": "email", "value": "hello@example.com"})
    assert result["status"] == "ok"
    assert sandbox.page.input_value("#email") == "hello@example.com"

    result = sandbox.execute_action({"action": "click", "target": "submit"})
    assert result["status"] == "ok"

    sandbox.close_browser()
    assert sandbox.browser_active is False


def test_memory_manager_supports_working_episodic_and_semantic_branches(tmp_path):
    db_path = tmp_path / "jarvis.db"
    memory = MemoryManager(db_path=str(db_path))

    memory.remember("working", "Finish the dinner plan for tonight.")
    memory.remember("episodic", "User asked for a quick pasta recipe yesterday.")
    memory.remember("semantic", "User prefers quick recipes under 20 minutes.")

    working_result = memory.recall("dinner plan", branch="working")
    episodic_result = memory.recall("pasta recipe", branch="episodic")
    semantic_result = memory.recall("quick recipes", branch="semantic")

    assert "dinner plan" in working_result[0]["content"]
    assert "pasta recipe" in episodic_result[0]["content"]
    assert "quick recipes" in semantic_result[0]["content"]

    memory.forget("semantic", "User prefers quick recipes under 20 minutes.")
    assert memory.recall("quick recipes", branch="semantic") == []

    consolidated = memory.consolidate()
    assert "working" in consolidated
    assert "episodic" in consolidated
    assert "semantic" in consolidated


def test_memory_manager_handles_natural_language_commands(tmp_path):
    db_path = tmp_path / "jarvis.db"
    memory = MemoryManager(db_path=str(db_path))

    response = memory.handle_command("remember episodic: User booked a grocery trip for Friday.")
    assert response["status"] == "ok"
    assert response["branch"] == "episodic"

    recall_response = memory.handle_command("recall episodic: grocery trip")
    assert recall_response["status"] == "ok"
    assert recall_response["matches"]

    forget_response = memory.handle_command("forget episodic: User booked a grocery trip for Friday.")
    assert forget_response["status"] == "ok"
    assert memory.recall("grocery trip", branch="episodic") == []

    consolidate_response = memory.handle_command("consolidate")
    assert consolidate_response["status"] == "ok"
    assert "working" in consolidate_response["summary"]


def test_extract_gemini_text_handles_nested_response_shapes():
    payload = {
        "candidates": [{
            "content": {
                "parts": [{"text": "Pesto is the best choice for pizza."}]
            }
        }]
    }
    assert extract_gemini_text(payload) == "Pesto is the best choice for pizza."

    payload_legacy = {"text": "Classic margherita works well."}
    assert extract_gemini_text(payload_legacy) == "Classic margherita works well."


def test_build_memory_context_includes_saved_user_preferences(tmp_path):
    db_path = tmp_path / "jarvis.db"
    memory = MemoryManager(db_path=str(db_path))
    memory.remember("working", "I am allergic to tomatoes.")
    memory.remember("semantic", "The user likes spicy sauces.")

    context = build_memory_context(memory)

    assert any("allergic to tomatoes" in item.lower() for item in context)
    assert any("spicy sauces" in item.lower() for item in context)


def test_supported_gemini_model_defaults_are_current_generation(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert get_gemini_model() in DEFAULT_GEMINI_MODELS
    assert "3.6" in get_gemini_model()


def test_build_google_auth_url_uses_supported_oauth_flow(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "demo-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "demo-secret")
    url = build_google_auth_url()

    assert "accounts.google.com/o/oauth2/v2/auth" in url
    assert "demo-client-id.apps.googleusercontent.com" in url
    assert "access_type=offline" in url
    assert "gmail.readonly" in url


def test_get_gmail_status_reports_not_authorized_without_tokens(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    status = get_gmail_status()

    assert status["status"] == "not_configured"
    assert "Google OAuth" in status["message"]


def test_parse_email_task_extracts_recipient_and_body():
    prompt = "email foo@example.com with a quick message saying hello there"
    result = parse_email_task(prompt)

    assert result["recipient"] == "foo@example.com"
    assert "hello" in result["body"].lower()
    assert result["subject"]


def test_infer_browser_target_detects_xeramail():
    target = infer_browser_target("go to xeramail and send a note to hello@example.com")

    assert target == "https://xeramail.com/"


def test_browser_agent_handles_xeramail_compose_flow():
    sandbox = SandboxedVM()
    sandbox.open_browser(
        "data:text/html,<html><body><input id='to' /><input id='subject' /><textarea id='message'></textarea><button id='send'>Send</button></body></html>"
    )
    agent = JarvisAgent(sandbox=sandbox)

    result = agent.run("Use xeramail to send hello@example.com a message saying hello there", url=None)

    assert result["status"] == "ok"
    assert any(entry.get("action") == "fill" and "to" in str(entry.get("target", "")) for entry in result["action_log"])
    assert any(entry.get("action") == "click" for entry in result["action_log"])

    sandbox.close_browser()


def test_send_gmail_message_uses_oauth_token(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = req.headers
        captured["body"] = req.data.decode("utf-8")
        return FakeResponse({"id": "abc123"})

    monkeypatch.setattr("server.request.urlopen", fake_urlopen)

    result = send_gmail_message("foo@example.com", "Hello", "This is a test.", access_token="token-123")

    assert result["status"] == "ok"
    assert "gmail.googleapis.com" in captured["url"]
    assert "token-123" in captured["headers"]["Authorization"]
    assert '"raw"' in captured["body"]
    assert "Q29udGVudC1UeXBl" in captured["body"]


def test_parse_doc_paste_task_detects_google_doc_url_and_poem_text():
    prompt = 'Use https://docs.google.com/document/d/abc123/edit?usp=sharing and paste the poem "The Raven" into the given google doc.'
    result = parse_doc_paste_task(prompt)

    assert result["url"].startswith("https://docs.google.com/document/")
    assert "raven" in result["text"].lower()
    assert result["target_kind"] == "google_doc"


def test_build_dom_paste_plan_keeps_google_doc_flow_dom_first():
    plan = build_dom_paste_plan('Use https://docs.google.com/document/d/abc123/edit and paste "The Raven" into the given google doc.')

    assert plan["target_kind"] == "google_doc"
    assert plan["url"].startswith("https://docs.google.com/document/")
    assert "raven" in plan["text"].lower()
    assert plan["mode"] == "dom_first_google_doc"
    assert "editable_targets" in plan
    assert "ready_to_paste" in plan["message"].lower()


def test_parse_doc_paste_task_ignores_read_only_document_requests():
    prompt = 'Use https://docs.google.com/document/d/abc123/edit?usp=sharing and tell me what text is in the shared doc. Return a text answer containing the name or a summary of the doc.'
    result = parse_doc_paste_task(prompt)

    assert result["url"].startswith("https://docs.google.com/document/")
    assert result["target_kind"] == "website"
    assert "tell me" in result["text"].lower()


def test_answer_google_doc_question_uses_reasoning_not_raw_document(monkeypatch):
    text = "The Raven By Edgar Allan Poe Once upon a midnight dreary, while I pondered, weak and weary..."

    monkeypatch.setattr("server.call_gemini", lambda prompt, api_key=None, context=None: "This is The Raven by Edgar Allan Poe.")

    result = answer_google_doc_question("what poem is this", text)

    assert result == "This is The Raven by Edgar Allan Poe."
    assert "Once upon a midnight dreary" not in result
    assert len(result) < 200


def test_strip_doc_context_removes_doc_memory_from_general_prompts():
    context = [
        "https://docs.google.com/document/d/abc123/edit",
        "Poem identified: The Raven by Edgar Allan Poe.",
        "General task: answer 1 + 1",
    ]

    result = strip_doc_context(context)

    assert result == ["General task: answer 1 + 1"]


def test_read_google_doc_summary_returns_title_and_excerpt():
    sandbox = SandboxedVM()
    sandbox.open_browser(
        "data:text/html,<html><title>Weekly Plan</title><body><h1>Weekly Plan</h1><p>Use this document to track groceries and chores.</p></body></html>"
    )

    result = read_google_doc_summary(sandbox.page.url)

    assert result["status"] == "ok"
    assert "Weekly Plan" in result["title"]
    assert "groceries" in result["summary"].lower()

    sandbox.close_browser()


def test_read_google_doc_summary_prefers_google_doc_export(monkeypatch):
    calls = {}

    def fake_fetch(url):
        calls["url"] = url
        return "Once upon a midnight dreary\n\nQuoth the Raven"

    monkeypatch.setattr("server.fetch_google_doc_export_text", fake_fetch)

    result = read_google_doc_summary("https://docs.google.com/document/d/abc123/edit")

    assert result["status"] == "ok"
    assert "midnight" in result["summary"].lower()
    assert "raven" in result["summary"].lower()
    assert calls["url"].startswith("https://docs.google.com/document/d/abc123/edit")


def test_sandbox_can_attempt_google_doc_paste():
    sandbox = SandboxedVM()
    sandbox.open_browser("data:text/html,<html><body><div contenteditable='true' id='doc'>Start</div></body></html>")

    result = sandbox.paste_text_into_document("The Raven", target_selector="#doc")
    assert result["status"] == "ok"
    assert "The Raven" in sandbox.page.locator("#doc").inner_text()

    sandbox.close_browser()


def test_sandbox_finds_google_doc_like_editable_targets():
    sandbox = SandboxedVM()
    sandbox.open_browser("data:text/html,<html><body><div role='textbox' aria-label='Document content'>Start</div></body></html>")

    candidates = sandbox.find_editable_targets()
    assert candidates
    assert any("textbox" in candidate.lower() or "contenteditable" in candidate.lower() for candidate in candidates)

    result = sandbox.paste_text_into_document("The Raven", target_selector=candidates[0])
    assert result["status"] == "ok"

    sandbox.close_browser()


def test_sandbox_supports_persistent_browser_profile(tmp_path):
    profile_dir = tmp_path / "playwright-profile"
    sandbox = SandboxedVM(profile_dir=str(profile_dir))
    sandbox.open_browser("https://example.com")

    assert sandbox.profile_dir == str(profile_dir)
    assert profile_dir.exists()
    assert sandbox.browser_active is True

    sandbox.close_browser()


def test_memory_manager_supports_auth_account_branch(tmp_path):
    db_path = tmp_path / "jarvis.db"
    memory = MemoryManager(db_path=str(db_path))

    response = memory.handle_command("remember auth: user@example.com")
    assert response["status"] == "ok"
    assert response["branch"] == "auth"

    matches = memory.recall("user@example.com", branch="auth")
    assert matches
    assert any("user@example.com" in item["content"] for item in matches)

    account = memory.get_auth_account()
    assert account == "user@example.com"
