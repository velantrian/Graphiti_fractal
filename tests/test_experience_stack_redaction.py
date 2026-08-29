from experience.models import ExperienceIngestRequest
from experience.writer import compute_context_hash


def test_experience_stack_redacts_secret_keys_before_hash_or_persistence():
    request = ExperienceIngestRequest(
        task_type="diagnostic",
        stack={
            "python": "3.12",
            "OPENAI_API_KEY": "sk-stack-secret",
            "nested": {
                "password": "nested-password",
                "safe": "visible",
            },
        },
    )

    assert request.stack == {
        "python": "3.12",
        "OPENAI_API_KEY": "[REDACTED]",
        "nested": {
            "password": "[REDACTED]",
            "safe": "visible",
        },
    }

    digest = compute_context_hash(request)
    assert digest
    assert "sk-stack-secret" not in repr(request.stack)
    assert "nested-password" not in repr(request.stack)


def test_experience_stack_redacts_embedded_bearer_and_assignment_forms():
    request = ExperienceIngestRequest(
        stack={
            "header": 'Authorization: Bearer "stack-bearer"',
            "env": "TOKEN='stack-token'",
            "note": "safe text",
        }
    )

    rendered = repr(request.stack)
    assert "stack-bearer" not in rendered
    assert "stack-token" not in rendered
    assert "safe text" in rendered
    assert "[REDACTED]" in rendered
