package model

import (
	"errors"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

// TransitionChannelHealth changes only an eligible single-account channel.
// The conditional update prevents a late recovery from undoing manual disable.
// Status, diagnostic reason and routing abilities commit together.
func TransitionChannelHealth(id, expected, next int, expectedReason, reason string) (bool, error) {
	// Serialize with manual status operations, including their cache updates.
	lock := GetChannelPollingLock(id)
	lock.Lock()
	defer lock.Unlock()
	changed := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var ch Channel
		err := lockForUpdate(tx).Where("id = ? AND status = ?", id, expected).First(&ch).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		if ch.ChannelInfo.IsMultiKey || expectedReason != "" && ch.GetOtherInfo()["status_reason"] != expectedReason {
			return nil
		}
		info := ch.GetOtherInfo()
		info["status_reason"], info["status_time"] = reason, common.GetTimestamp()
		ch.SetOtherInfo(info)
		result := tx.Model(&Channel{}).Where("id = ? AND status = ?", id, expected).
			Updates(map[string]any{"status": next, "other_info": ch.OtherInfo})
		if result.Error != nil || result.RowsAffected != 1 {
			return result.Error
		}
		if err := tx.Model(&Ability{}).Where("channel_id = ?", id).Update("enabled", next == common.ChannelStatusEnabled).Error; err != nil {
			return err
		}
		changed = true
		return nil
	})
	if err == nil && changed {
		InitChannelCache()
	}
	return changed, err
}
