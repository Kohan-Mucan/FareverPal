"""User planner data: the farm list and the craft queue in planner.json.

`planner` owns that one file — its schema, its cache and its writers. Pure
data storage: no Qt and no game reads, so both the Items page (farm list,
owned gear) and the Craft page (queue) import it.
"""
