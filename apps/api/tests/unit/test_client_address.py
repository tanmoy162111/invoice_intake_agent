import pytest

from intake.api.auth import client_address, parse_networks

NETS = parse_networks("10.0.0.0/8, 192.168.1.5, ::1")


def test_without_a_trusted_proxy_the_connection_is_the_client() -> None:
    assert client_address("203.0.113.7", "1.2.3.4", []) == "203.0.113.7"
    assert client_address("203.0.113.7", None, NETS) == "203.0.113.7"


def test_a_forwarded_address_is_believed_only_from_a_trusted_peer() -> None:
    assert client_address("10.0.0.5", "198.51.100.9", NETS) == "198.51.100.9"
    assert client_address("203.0.113.7", "198.51.100.9", NETS) == "203.0.113.7"  # spoofing attempt
    assert client_address("192.168.1.5", "198.51.100.9", NETS) == "198.51.100.9"
    assert client_address("::1", "2001:db8::7", NETS) == "2001:db8::7"


def test_a_chain_of_proxies_gives_the_last_address_that_is_not_ours() -> None:
    # a client, a proxy we do not run, then one of ours: the untrusted hop nearest us is the client
    assert (
        client_address("10.0.0.5", "198.51.100.9, 203.0.113.50, 10.0.0.9", NETS) == "203.0.113.50"
    )
    # what an attacker put at the front of the header cannot override the real hop
    assert client_address("10.0.0.5", "1.1.1.1, 198.51.100.9", NETS) == "198.51.100.9"


@pytest.mark.parametrize("header", ["", "  ", "garbage", "999.1.1.1", ",,,", "10.0.0.1, 10.0.0.2"])
def test_a_useless_forwarded_header_falls_back_to_the_peer(header: str) -> None:
    assert client_address("10.0.0.5", header, NETS) == "10.0.0.5"


def test_a_missing_peer_is_its_own_client_name() -> None:
    assert client_address(None, "198.51.100.9", NETS) == "unknown"


def test_a_peer_that_is_not_an_address_is_never_trusted() -> None:
    assert client_address("testclient", "198.51.100.9", NETS) == "testclient"


@pytest.mark.parametrize("text", ["", "  ", " , "])
def test_no_trusted_proxies_by_default(text: str) -> None:
    assert parse_networks(text) == []


@pytest.mark.parametrize("text", ["not-an-ip", "10.0.0.0/33", "10.0.0.0/8, nope"])
def test_a_bad_trusted_proxy_setting_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        parse_networks(text)
