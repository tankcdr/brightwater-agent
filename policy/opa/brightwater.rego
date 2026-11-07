# Brightwater authorization policy (Ch 8), evaluated by OPA.
#
# Two decisions:
#   data.brightwater.allow       agentgateway asks before forwarding a request to the AIP server
#   data.brightwater.tool_allow  the agent's tool wrapper asks before every tool call
#
# Publishing a procedure is shipping code: every client that runs it executes its scripts.
# So catalog writes (publish, pin, retire) and governance writes need the procedure_author role.
package brightwater

import rego.v1

catalog_write if {
	input.method in {"POST", "PUT", "DELETE"}
	startswith(input.path, "/catalog")
}

governance_write if {
	input.method in {"PUT", "DELETE"}
	startswith(input.path, "/governance")
}

author if "procedure_author" in input.roles

banker if "banker" in input.roles

default allow := false

# Reading the catalog and running procedures: any banker or author.
allow if {
	not catalog_write
	not governance_write
	some role in {"banker", "procedure_author"}
	role in input.roles
}

allow if {
	catalog_write
	author
}

allow if {
	governance_write
	author
}

# Tools that read no customer data.
public_tools := {"get_policy"}

default tool_allow := false

tool_allow if {
	banker
	input.tool in public_tools
}

# Every other tool reads one application's data: the banker must be entitled to it,
# which OpenFGA decides and the wrapper passes in as input.entitled.
tool_allow if {
	banker
	not input.tool in public_tools
	input.entitled == true
}
