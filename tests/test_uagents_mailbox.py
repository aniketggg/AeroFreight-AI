"""Tests for mailbox-only uAgents registration."""

from uagents.registration import AlmanacApiRegistrationPolicy

from orchestrator.uagents_mailbox import mailbox_registration_policy


def test_mailbox_registration_policy_is_api_only():
    policy = mailbox_registration_policy()
    assert isinstance(policy, AlmanacApiRegistrationPolicy)
