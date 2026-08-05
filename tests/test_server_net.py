"""Server diagnostics net helpers — region label resolution for captured
connections. Pure-function tests: no widgets, no game process."""

import pytest

pytest.importorskip("PySide6")

from farever_companion.ui.pages.server import net


def test_region_game_ips_get_their_region_label():
    """Every hardcoded per-region game IP must resolve to its own region,
    never 'Unassigned' and never the shared 'Global Login' control plane."""
    for code, hosts in net.CAPTURED_GAME_SERVERS.items():
        region = next((r for r in net.REGIONS if r["code"] == code), None)
        assert region, f"region {code} missing from REGIONS"
        for ip, port in hosts:
            label = net._label_for_connection(ip, port)
            assert label == region["label"], f"{ip}:{port} ({code}) -> {label!r}"
            # a different port on the same host still falls back to IP-only
            assert net._label_for_connection(ip, port + 1) == region["label"]


def test_login_gateways_label_global_login():
    for ip, _ in net.LOGIN_GATEWAYS:
        assert net._label_for_connection(ip) == "Global Login"


def test_master_hostnames_label_their_group():
    """Masters are hostnames; the TCP table only shows resolved IPs, so the
    reverse-DNS hostname must drive the match. Global masters are control
    plane, CN masters sit under the China card (same as the ping panel)."""
    for host, _ in net.MASTER_HOSTS["global"]:
        assert net._label_for_connection("203.0.113.1", hostname=host) == "Global Login"
    for host, _ in net.MASTER_HOSTS["cn"]:
        assert net._label_for_connection("203.0.113.1", hostname=host) == "China"


def test_game_hostname_not_mislabeled_as_master():
    assert net._label_for_connection("203.0.113.1", hostname="na1.farever.net") == ""


def test_reverse_dns_failure_hostname_is_plain_ip():
    """_reverse_dns returns the raw IP when lookup fails — that must not trip
    the master-hostname check."""
    assert net._label_for_connection("203.0.113.1", hostname="203.0.113.1") == ""


def test_user_added_ips_keep_their_region():
    custom = {"eu": [["1.2.3.4", 6100]]}
    assert net._label_for_connection("1.2.3.4", custom_hosts=custom) == "Europe"
    # A host in another region's list must not match
    other = {"na": [["5.6.7.8", 6200]]}
    assert net._label_for_connection("1.2.3.4", custom_hosts=other) == ""
    # Region game IPs outrank a user assignment to a different region
    na_ip = net.CAPTURED_GAME_SERVERS["na"][0][0]
    assert net._label_for_connection(na_ip, custom_hosts={"eu": [[na_ip, 9999]]}) == "North America"


def test_malformed_custom_hosts_entries_are_ignored():
    custom = {"eu": [["1.2.3.4", 6100], [], None], "na": None}
    assert net._label_for_connection("1.2.3.4", custom_hosts=custom) == "Europe"


def test_unknown_ip_returns_empty():
    assert net._label_for_connection("203.0.113.9") == ""
