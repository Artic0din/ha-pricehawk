# CDR Incentive Shape Catalog v3 (in-scope $/yr math)

_Sweep: 10262 plans, 7165 incentives_
_Source: /tmp/cdr-cache/_
_Scope: incentives that affect recurring $/yr cost. v3 broadened stepped_fit to catch Origin+AGL+Solar Max._

## Coverage — IN-SCOPE rules

| rule_id | incentives | plans | retailers |
|---|---:|---:|---:|
| stepped_fit_rate_first | 66 | 66 | 1 |
| stepped_fit_quantity_first | 40 | 40 | 1 |
| solar_max_export_pool | 104 | 104 | 2 |
| bonus_fit_capped_windowed | 20 | 20 | 1 |
| bonus_fit_uncapped_windowed | 70 | 70 | 1 |
| free_import_window | 315 | 315 | 4 |
| behavior_daily_credit | 20 | 20 | 1 |
| critical_peak_export | 20 | 20 | 1 |
| critical_peak_import | 20 | 20 | 1 |
| vpp_rebate | 693 | 687 | 2 |
| ev_offpeak_override | 165 | 165 | 2 |
| ovo_credit_interest | 324 | 324 | 1 |
| subscription_bundle_with_dollar_value | 150 | 150 | 1 |

**IN-SCOPE total: 2007 incentives (28.0%)**


## Dropped — OUT-OF-SCOPE per user

| dropped category | incentives | plans |
|---|---:|---:|
| loyalty_points | 528 | 528 |
| charity_donation | 637 | 482 |
| signup_credit_oneoff | 1238 | 1163 |
| referral_credit | 517 | 517 |
| prepaid_card_bonus | 172 | 172 |
| perk_membership | 553 | 553 |
| greenpower_flag | 422 | 422 |
| solar_install_offer | 10 | 10 |
| marketing_copy | 893 | 893 |

**Dropped: 4970 (69.4%)**

**Still-UNMATCHED: 188 (2.6%)**


## IN-SCOPE samples

### stepped_fit_rate_first  (66, 1 retailer(s))
_Tiered FIT, rate-first: 'X c/kWh until N kWh' (Origin/Alinta)_
Retailers: alinta-energy

- **[alinta-energy]** *Solar Feed-in Tariff*
    elig: `This Energy Plan includes a stepped feed-in tariff, where you will receive a feed-in of 7c/kWh for the first 10kW exported. For any export after that you will obtain Alinta Energy’s standard retailer feed-in tariff of 0.04c/kWh.`
- **[alinta-energy]** *Stepped FiT*

### stepped_fit_quantity_first  (40, 1 retailer(s))
_Tiered FIT, quantity-first: 'first N kWh ... at X c/kWh' (AGL/GloBird)_
Retailers: agl

- **[agl]** *Solar Feed-in Tarriff*
    elig: `This plan features a tiered feed-in tariff. For the first 10kWh exported each day, we’ll pay you a higher feed-in tariff of 6c/kWh. Then, we’ll pay 1.5c/kWh for the rest of that day`

### solar_max_export_pool  (104, 2 retailer(s))
_Solar Max / monthly daily-averaged export pool (Origin)_
Retailers: energyaustralia, origin-energy

- **[origin-energy]** *Solar feed-in tariffs*
    elig: `Origin offers 12 cents per kWh until a daily export limit of 8 kWh is reached. The daily export limit is averaged across your billing period (calculated by multiplying the number of days in your billing period by your daily export limit of `
- **[origin-energy]** *Solar feed-in tariffs*
    elig: `Origin offers 4 cents per kWh until a daily export limit of 8 kWh is reached. The daily export limit is averaged across your billing period (calculated by multiplying the number of days in your billing period by your daily export limit of 8`
- **[origin-energy]** *Solar feed-in tariffs*
    elig: `Origin offers 5 cents per kWh until a daily export limit of 8 kWh is reached. The daily export limit is averaged across your billing period (calculated by multiplying the number of days in your billing period by your daily export limit of 8`
- **[energyaustralia]** *Solar Max*
    elig: `Solar Max is for electricity only and is available to eligible residential solar customers not receiving any Government feed-in-tariff. The daily export is averaged by dividing the total solar export by the number of days in each billing pe`

### bonus_fit_capped_windowed  (20, 1 retailer(s))
_Bonus FIT: extra c/kWh on first N kWh exported in window (ZEROHERO Super Export)_
Retailers: globird-energy

- **[globird-energy]** *Super Export Credit*
    elig: `15 cents/kWh applies to the first 15 kWh of exports between 6pm-9pm (Local Time) everyday, and is inclusive of any other Feed-in tariff as applicable in Energy Plan.`
- **[globird-energy]** *Super Export Credit*

### bonus_fit_uncapped_windowed  (70, 1 retailer(s))
_Bonus FIT: extra c/kWh on all exports in window (Peak solar feed-in)_
Retailers: globird-energy

- **[globird-energy]** *Peak solar feed-in*
    elig: `5 cents/kWh applies to exports between 4pm-11pm (Local Time) everyday.`
- **[globird-energy]** *Peak solar feed-in*
    elig: `3 cents/kWh applies to exports between 4pm-11pm (Local Time) everyday.`
- **[globird-energy]** *Peak solar feed-in*
    elig: `2 cents/kWh applies to exports between 4pm-11pm (Local Time) everyday.`

### free_import_window  (315, 4 retailer(s))
_Free import window (3-for-Free, OVO Free 3, Four-hour free)_
Retailers: agl, globird-energy, myob-powered-by-ovo, red-energy

- **[myob-powered-by-ovo]** *Free 3*
- **[myob-powered-by-ovo]** *Free 3*
    elig: `Free electricity between 11am and 2pm everyday. Does not apply to controlled loads. For more information head to https://pages.ovoenergy.com.au/the-free-3-plan`
- **[agl]** *Three for Free Usage*
- **[globird-energy]** *Four-hour free usage every day*
    elig: `$0.00 for consumption between 10am-2pm (Local Time), excluding controlled load.`

### behavior_daily_credit  (20, 1 retailer(s))
_$X/day fixed credit conditional on consumption behavior_
Retailers: globird-energy

- **[globird-energy]** *ZEROHERO Credit*
    elig: `$1/Day when imports are 0.03 kWh/hour or less, between 6pm-9pm (Local Time).`
- **[globird-energy]** *ZEROHERO Credit*

### critical_peak_export  (20, 1 retailer(s))
_Per-event $X/kWh export credit (event-driven)_
Retailers: globird-energy

- **[globird-energy]** *Critical Peak-Export Credit*
    elig: `$1/kWh applies to any export during a Critical Peak-Export event. The timing of these events is determined at our discretion, as detailed in a notice we provide. Your premises' metering installation must support 5-minute interval data.`
- **[globird-energy]** *Critical Peak-Export Credit*

### critical_peak_import  (20, 1 retailer(s))
_Per-event credit for importing during peak event_
Retailers: globird-energy

- **[globird-energy]** *Critical Peak-Import Credit*
    elig: `5 cents/kWh applies to any import during a Critical Peak-Import event. The timing of these events is determined at our discretion, as detailed in a notice we provide. Your premises' metering installation must support 5-minute interval data.`
- **[globird-energy]** *Critical Peak-Import Credit*

### vpp_rebate  (693, 2 retailer(s))
_VPP/demand response rebate (event-driven)_
Retailers: energyaustralia, engie

- **[engie]** *ENGIE VPP credits*
    elig: `Receive $100 (GST exempt) sign-up credit as well as approx $15 monthly credit per battery for participating in our VPP, which is calculated by multiplying 0.493150c (GST exempt) by the number of days in a month applied on your next bill.`
- **[engie]** *ENGIE VPP Credits*
- **[energyaustralia]** *PowerResponse program rebate*
    elig: `You may be eligible for our PowerResponse program, and by participating in events, you may be eligible for rebates which may change over time. See website energyaustralia.com.au/power-response for details on eligibility criteria, T&C’s and `
- **[energyaustralia]** *PowerResponse program rebate*

### ev_offpeak_override  (165, 2 retailer(s))
_EV off-peak rate override (OVO/ENGIE)_
Retailers: engie, myob-powered-by-ovo

- **[myob-powered-by-ovo]** *EV Off-Peak*
- **[myob-powered-by-ovo]** *Electric Vehicle Off-Peak*
    elig: `$0.045/kWh usage charge between midnight and 6am. Does not apply to controlled loads. For more information head to https://www.ovoenergy.com.au/electric-vehicles/`
- **[myob-powered-by-ovo]** *Electric Vehicle Off-Peak*
    elig: `$0.04725/kWh usage charge between midnight and 6am. Does not apply to controlled loads. For more information head to https://www.ovoenergy.com.au/electric-vehicles/`
- **[engie]** *EV Flex Charge*

### ovo_credit_interest  (324, 1 retailer(s))
_OVO 3% interest on credit balances_
Retailers: myob-powered-by-ovo

- **[myob-powered-by-ovo]** *Interest Rewards*
- **[myob-powered-by-ovo]** *Interest Rewards*
    elig: `OVO Energy pay 3% interest on credit balances (after all monthly charges are considered). This is prorated for the number of days since your last bill.`

### subscription_bundle_with_dollar_value  (150, 1 retailer(s))
_Bundled streaming subscription with $ value_
Retailers: agl

- **[agl]** *Netflix Standard with ads included*
    elig: `Netflix Standard with ads is included in this plan. Optional: upgrade your Netflix tier to Standard or Premium at an additional cost`
- **[agl]** *Netflix Standard with ads*


## TOP 25 still-UNMATCHED

| count | displayName | sample eligibility |
|---:|---|---|
| 168 | Solar feed-in tariffs | The Terms and Conditions for Feed-in Tariffs – Victoria applies to both additional and standard retailer feed-in tariff. When the benefit period ends you’ll rec |
| 20 | Generous solar feed-in | (empty) |