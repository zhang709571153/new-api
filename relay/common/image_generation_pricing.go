package common

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	hosttypes "github.com/QuantumNous/new-api/types"
	"github.com/shopspring/decimal"
)

// ReserveImageGenerationPricing freezes the image tariff with the request and
// includes one maximum-priced output in its normal refundable reservation.
// Responses may choose to produce no image, or more than one; final outputs
// remain authoritative at settlement, like the other Responses tools.
func ReserveImageGenerationPricing(info *RelayInfo, price *hosttypes.PriceData) error {
	if info.ResponsesUsageInfo == nil {
		return nil
	}
	if _, ok := info.BuiltInTools[dto.BuildInToolImageGeneration]; !ok {
		return nil
	}
	pricing, err := operation_setting.GetImageGenerationPricing()
	if err != nil {
		return err
	}
	info.ImagePricing = pricing
	if pricing == nil {
		return nil
	}
	reserved := pricing.PriceCNY("4K").Div(decimal.NewFromFloat(pricing.USDExchangeRate)).
		Mul(decimal.NewFromFloat(common.QuotaPerUnit))
	total, clamp := common.QuotaFromDecimalChecked(decimal.NewFromInt(int64(price.QuotaToPreConsume)).Add(reserved.Ceil()))
	if clamp != nil {
		info.QuotaClamp = clamp
	}
	price.QuotaToPreConsume = total
	price.FreeModel = false
	info.PriceData = *price
	return nil
}
