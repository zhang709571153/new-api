package controller

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

func TestWorkspaceSubscriptionCancellationFundingContext(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	personal, err := model.SetWorkspaceWeeklySubscription(owner.Id, 0, 100)
	require.NoError(t, err)
	// Recreate a legacy team and take the team-view confirmation snapshot.
	// An empty migration intentionally leaves this personal subscription alone.
	require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
	require.NoError(t, model.DB.Delete(&model.WorkspaceTeamAccount{}, "team_id = ?", team.ID).Error)
	require.NoError(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{}, 0))
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	require.Zero(t, personal.WorkspaceTeamID)
	router := gin.New()
	router.POST("/subscriptions/:id/invalidate", func(c *gin.Context) { c.Set("role", common.RoleRootUser); AdminInvalidateUserSubscription(c) })
	call := func(subID, scopeID int, context string) *httptest.ResponseRecorder {
		res := httptest.NewRecorder()
		body := fmt.Sprintf(`{"expected_scope":{"user_id":%d,"workspace_team_id":%d}%s}`, owner.Id, scopeID, context)
		router.ServeHTTP(res, httptest.NewRequest("POST", fmt.Sprintf("/subscriptions/%d/invalidate", subID), strings.NewReader(body)))
		return res
	}
	oldContext := fmt.Sprintf(`,"funding_context":{"team_id":%d,"funding_version":0}`, team.ID)
	res := call(personal.Id, 0, oldContext)
	require.False(t, gjson.Get(res.Body.String(), "success").Bool(), res.Body.String())
	require.Contains(t, res.Body.String(), "团队信息已变化")
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	require.Equal(t, "active", personal.Status)
	// An explicit personal action remains valid without a team-view context.
	require.True(t, gjson.Get(call(personal.Id, 0, "").Body.String(), "success").Bool())
	teamSub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 100)
	require.NoError(t, err)
	currentContext := fmt.Sprintf(`,"funding_context":{"team_id":%d,"funding_version":1}`, team.ID)
	require.True(t, gjson.Get(call(teamSub.Id, team.ID, currentContext).Body.String(), "success").Bool())
}
