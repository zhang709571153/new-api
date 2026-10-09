package model

import (
	"path/filepath"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

// Exercise the real startup path. SQLite deliberately bypasses AutoMigrate for
// subscription_plans, so a test that only AutoMigrates model structs misses it.
func TestWorkspaceSQLiteStartupSchema(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		name := "fresh"
		if legacy {
			name = "legacy"
		}
		t.Run(name, func(t *testing.T) {
			previousDB, previousLog := DB, LOG_DB
			previousPath, previousMaster := common.SQLitePath, common.IsMasterNode
			previousMainType, previousLogType := common.MainDatabaseType(), common.LogDatabaseType()
			t.Setenv("SQL_DSN", "")
			t.Setenv("LOG_SQL_DSN", "")
			common.SQLitePath = filepath.Join(t.TempDir(), "startup.db")
			common.IsMasterNode = true
			t.Cleanup(func() {
				if DB != nil && DB != previousDB {
					if sqlDB, err := DB.DB(); err == nil {
						_ = sqlDB.Close()
					}
				}
				DB, LOG_DB = previousDB, previousLog
				common.SQLitePath, common.IsMasterNode = previousPath, previousMaster
				common.SetDatabaseTypes(previousMainType, previousLogType)
				initCol()
			})
			if legacy {
				seed, err := gorm.Open(sqlite.Open(common.SQLitePath), &gorm.Config{})
				require.NoError(t, err)
				for _, ddl := range []string{
					`CREATE TABLE subscription_plans (id integer PRIMARY KEY, title varchar(128) NOT NULL, price_amount decimal(10,6) NOT NULL, currency varchar(8) NOT NULL DEFAULT 'USD')`,
					`INSERT INTO subscription_plans(id,title,price_amount,currency) VALUES(71,'Legacy preserved plan',12.34,'USD')`,
					`CREATE TABLE workspace_members (user_id integer PRIMARY KEY, team_id integer, token_id integer, status integer, created_at bigint)`,
					`CREATE TABLE subscription_pre_consume_records (id integer PRIMARY KEY, request_id varchar(64), user_id integer, user_subscription_id integer, pre_consumed bigint, wallet_pre_consumed bigint, funding_mode varchar(24), weekly_reset_at bigint, status varchar(32), created_at bigint, updated_at bigint)`,
					`INSERT INTO subscription_pre_consume_records VALUES(72,'startup-preserved-record',7,3,17,0,'subscription_first',1900000000,'settled',1700000000,1700000001)`,
					`CREATE TABLE subscription_orders (id integer PRIMARY KEY, user_id integer, plan_id integer, money real, trade_no varchar(255), status varchar(50))`,
					`INSERT INTO subscription_orders VALUES(73,7,71,12.34,'startup-preserved-order','success')`,
				} {
					require.NoError(t, seed.Exec(ddl).Error)
				}
				sqlDB, err := seed.DB()
				require.NoError(t, err)
				require.NoError(t, sqlDB.Close())
			}
			var createdPlan SubscriptionPlan
			for startup := range 2 {
				require.NoError(t, InitDB())
				for _, column := range []struct{ table, name string }{
					{"subscription_plans", "funding_scope"},
					{"workspace_members", "weekly_quota"},
					{"subscription_pre_consume_records", "workspace_member_user_id"},
					{"subscription_pre_consume_records", "workspace_token_id"},
					{"subscription_orders", "wallet_credit_quota"},
				} {
					require.True(t, DB.Migrator().HasColumn(column.table, column.name), "%s.%s missing on actual startup", column.table, column.name)
				}
				require.True(t, DB.Migrator().HasTable(&WorkspaceMemberWeeklyUsage{}))
				if startup == 0 {
					createdPlan = SubscriptionPlan{Title: "Startup team plan", FundingScope: "team", Currency: "CNY", PriceAmount: 99, WeeklyAmount: 100}
					require.NoError(t, DB.Create(&createdPlan).Error, "team plan writes must work on a fresh actual-startup schema")
					require.NoError(t, DB.Create(&Token{Id: 93, UserId: 94, WorkspaceUserID: 91, Key: "startup-fixture-key", Status: common.TokenStatusEnabled, ExpiredTime: -1}).Error)
					require.NoError(t, DB.Create(&WorkspaceMember{UserID: 91, TeamID: 92, TokenID: 93, WeeklyQuota: common.GetPointer(int64(100))}).Error)
					require.NoError(t, DB.Create(&WorkspaceTeam{ID: 92, OwnerUserID: 94, Name: "Startup fixture"}).Error)
					require.NoError(t, DB.Create(&WorkspaceMemberWeeklyUsage{TeamID: 92, UserID: 91, SubscriptionID: 95, WeeklyResetAt: 1900000000, UsedQuota: 7}).Error)
					if legacy {
						require.NoError(t, DB.Exec("UPDATE subscription_orders SET wallet_credit_quota = 123 WHERE id = 73").Error)
					}
				} else {
					var plan SubscriptionPlan
					require.NoError(t, DB.First(&plan, createdPlan.Id).Error)
					require.Equal(t, "team", plan.FundingScope)
					var member WorkspaceMember
					require.NoError(t, DB.First(&member, "user_id = ?", 91).Error)
					require.NotNil(t, member.WeeklyQuota)
					require.EqualValues(t, 100, *member.WeeklyQuota)
					var usage WorkspaceMemberWeeklyUsage
					require.NoError(t, DB.First(&usage, "team_id = ? AND user_id = ?", 92, 91).Error)
					require.EqualValues(t, 7, usage.UsedQuota)
				}
				if legacy {
					var plan SubscriptionPlan
					require.NoError(t, DB.First(&plan, 71).Error)
					require.Equal(t, "personal", plan.FundingScope)
					require.Equal(t, "Legacy preserved plan", plan.Title)
					require.Equal(t, 12.34, plan.PriceAmount)
					var record SubscriptionPreConsumeRecord
					require.NoError(t, DB.First(&record, 72).Error)
					require.EqualValues(t, 17, record.PreConsumed)
					require.EqualValues(t, 1700000001, record.UpdatedAt)
					require.Equal(t, "settled", record.Status)
					require.Zero(t, record.WorkspaceMemberUserID)
					require.Zero(t, record.WorkspaceTokenID)
					var order struct {
						WalletCreditQuota int64
						Money             float64
						Status            string
					}
					require.NoError(t, DB.Table("subscription_orders").Where("id = ?", 73).First(&order).Error)
					require.Equal(t, 12.34, order.Money)
					require.Equal(t, "success", order.Status)
					require.EqualValues(t, 123, order.WalletCreditQuota)
				}
				if startup == 0 {
					sqlDB, err := DB.DB()
					require.NoError(t, err)
					require.NoError(t, sqlDB.Close())
				}
			}
		})
	}
}
