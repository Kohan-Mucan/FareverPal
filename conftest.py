# Root conftest — exclude the ai/workspace scratch directory from test
# collection so ad-hoc probe scripts never break the suite.
collect_ignore_glob = ["ai/**"]
