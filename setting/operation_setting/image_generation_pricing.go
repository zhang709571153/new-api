package operation_setting

import (
	"fmt"
	"math"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
)

const ImageGenerationPricingOption = "ImageGenerationPricing"

// ImageGenerationPricing is a CNY image-only tariff, independent of text groups.
// The exchange rate is captured at request admission and never reread at settle.
type ImageGenerationPricing struct {
	Enabled         bool               `json:"enabled"`
	BaseCNY         map[string]float64 `json:"base_cny"`
	Multiplier      float64            `json:"multiplier"`
	USDExchangeRate float64            `json:"-"`
}

func ParseImageGenerationPricing(value string) (*ImageGenerationPricing, error) {
	var pricing ImageGenerationPricing
	if err := common.UnmarshalJsonStr(value, &pricing); err != nil {
		return nil, fmt.Errorf("invalid image generation pricing: %w", err)
	}
	if !pricing.Enabled {
		return nil, nil
	}
	if pricing.Multiplier <= 0 || pricing.Multiplier > 100 || math.IsNaN(pricing.Multiplier) || math.IsInf(pricing.Multiplier, 0) {
		return nil, fmt.Errorf("image multiplier must be finite and within (0, 100]")
	}
	if len(pricing.BaseCNY) != 3 {
		return nil, fmt.Errorf("image pricing requires 1K, 2K and 4K prices")
	}
	previous := 0.0
	for _, tier := range []string{"1K", "2K", "4K"} {
		price := pricing.BaseCNY[tier]
		if price <= 0 || price > 100 || price < previous || math.IsNaN(price) || math.IsInf(price, 0) {
			return nil, fmt.Errorf("image base prices must be finite, positive, ordered and at most CNY 100")
		}
		previous = price
	}
	return &pricing, nil
}

func GetImageGenerationPricing() (*ImageGenerationPricing, error) {
	common.OptionMapRWMutex.RLock()
	value := common.OptionMap[ImageGenerationPricingOption]
	rate := common.OptionMap["USDExchangeRate"]
	common.OptionMapRWMutex.RUnlock()
	if value == "" {
		return nil, nil
	}
	pricing, err := ParseImageGenerationPricing(value)
	if err != nil || pricing == nil {
		return pricing, err
	}
	pricing.USDExchangeRate, err = strconv.ParseFloat(rate, 64)
	if err != nil || pricing.USDExchangeRate <= 0 || math.IsNaN(pricing.USDExchangeRate) || math.IsInf(pricing.USDExchangeRate, 0) {
		return nil, fmt.Errorf("image pricing requires a positive finite USD exchange rate")
	}
	return pricing, nil
}

func (p *ImageGenerationPricing) PriceCNY(tier string) decimal.Decimal {
	base, ok := p.BaseCNY[tier]
	if !ok {
		base = p.BaseCNY["1K"]
	} // Unknown output dimensions never incur a higher tier.
	return decimal.NewFromFloat(base).Mul(decimal.NewFromFloat(p.Multiplier))
}
