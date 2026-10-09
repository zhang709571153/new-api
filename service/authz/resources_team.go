package authz

const ResourceTeam = "team"

var (
	TeamRead  = Permission{Resource: ResourceTeam, Action: ActionRead}
	TeamWrite = Permission{Resource: ResourceTeam, Action: ActionWrite}
)

func init() {
	RegisterResource(ResourceDefinition{
		Resource: ResourceTeam,
		LabelKey: "Team management",
		Actions: []ActionDefinition{
			{Action: ActionRead, LabelKey: "View team usage", DescriptionKey: "View member allowances and usage", DefaultRoles: []string{BuiltInRoleAdmin}},
			{Action: ActionWrite, LabelKey: "Manage teams", DescriptionKey: "Create users and manage team names, members and allowances", DefaultRoles: []string{BuiltInRoleAdmin}},
		},
	})
}
