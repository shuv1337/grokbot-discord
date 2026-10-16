from grokbot_discord.routing import addressed_by_name, route
from tests.helpers import make_msg


def test_alias_first_token(bots):
    msg = make_msg(content="<@bridge> loops ping", mention_user_ids=frozenset({"bridge"}))
    hit = route(msg, bots, self_user_id="bridge", default_bot="loops")
    assert hit is not None
    assert hit.bot.name == "loops"
    assert hit.text == "ping"


def test_role_mention(bots):
    msg = make_msg(
        content="<@&role-xo> status",
        mention_user_ids=frozenset(),
        mention_role_ids=frozenset({"role-xo"}),
    )
    hit = route(msg, bots, self_user_id="bridge", default_bot="loops")
    assert hit is not None
    assert hit.bot.name == "xo"
    assert "status" in hit.text


def test_multi_role_is_unknown(bots):
    msg = make_msg(
        content="hey",
        mention_user_ids=frozenset({"bridge"}),
        mention_role_ids=frozenset({"role-loops", "role-xo"}),
    )
    assert route(msg, bots, self_user_id="bridge", default_bot="loops") is None


def test_default_when_only_bot_mentioned(bots):
    msg = make_msg(content="<@bridge> what is in flight?", mention_user_ids=frozenset({"bridge"}))
    hit = route(msg, bots, self_user_id="bridge", default_bot="loops")
    assert hit is not None
    assert hit.bot.name == "loops"
    assert "what is in flight?" in hit.text


def test_unknown_name(bots):
    msg = make_msg(content="<@bridge> no-such ping", mention_user_ids=frozenset({"bridge"}))
    hit = route(msg, bots, self_user_id="bridge", default_bot="")
    assert hit is None


def test_bare_name_prefix(bots):
    msg = make_msg(content="loops ping", mention_user_ids=frozenset())
    hit = route(msg, bots, self_user_id="bridge", default_bot="loops")
    assert hit is not None
    assert hit.bot.name == "loops"
    assert hit.text == "ping"


def test_at_name_prefix(bots):
    msg = make_msg(content="@xo status", mention_user_ids=frozenset())
    hit = route(msg, bots, self_user_id="bridge", default_bot="loops")
    assert hit is not None
    assert hit.bot.name == "xo"
    assert hit.text == "status"


def test_addressed_by_name(bots):
    assert addressed_by_name("loops ping", bots)
    assert addressed_by_name("@XO status", bots)
    assert not addressed_by_name("please ping loops", bots)
    assert not addressed_by_name("<@bridge> ping", bots, self_user_id="bridge")
    assert addressed_by_name("<@bridge> loops ping", bots, self_user_id="bridge")


def test_name_only_routing_ignores_role_and_never_uses_default(bots):
    from tests.helpers import make_msg

    hit = route(
        make_msg(content="loops <@&role-xo> do work", mention_role_ids=frozenset({"role-xo"})),
        bots,
        self_user_id="bridge",
        default_bot="xo",
        named_only=True,
    )
    assert hit.bot.name == "loops"
    assert (
        route(
            make_msg(content="<@bridge> do work", mention_role_ids=frozenset({"role-xo"})),
            bots,
            self_user_id="bridge",
            default_bot="xo",
            named_only=True,
        )
        is None
    )
