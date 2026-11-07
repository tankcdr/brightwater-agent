package brightwater_test

import rego.v1

import data.brightwater

test_banker_can_run if brightwater.allow with input as {"method": "POST", "path": "/runs", "roles": ["banker"]}

test_banker_cannot_publish if not brightwater.allow with input as {"method": "POST", "path": "/catalog", "roles": ["banker"]}

test_author_can_publish if brightwater.allow with input as {"method": "POST", "path": "/catalog", "roles": ["procedure_author"]}

test_nobody_without_role if not brightwater.allow with input as {"method": "GET", "path": "/catalog", "roles": []}

test_tool_needs_entitlement if not brightwater.tool_allow with input as {"tool": "get_figures", "roles": ["banker"], "entitled": false}

test_tool_with_entitlement if brightwater.tool_allow with input as {"tool": "get_figures", "roles": ["banker"], "entitled": true}

test_policy_tool_is_public if brightwater.tool_allow with input as {"tool": "get_policy", "roles": ["banker"]}
