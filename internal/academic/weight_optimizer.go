package academic

import (
	"context"
	"fmt"
	"math"
	"sort"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const WeightModelVersion = "course-weight-optimizer-d70349b-group-v1"
const weightEpsilon = 1e-9

var weightScenarios = []string{"conservative", "neutral", "aggressive"}

type WeightPolicy struct {
	Budget, Minimum, Step, NodeLimit int
	TimeLimit                        time.Duration
}

type WeightMarketCourse struct {
	ID                string
	Capacity, Bidders int
}

type WeightCandidate struct {
	ID, Name                     string
	Capacity, Bidders            int
	Utility                      float64
	Groups                       []string
	AlreadySelected, TimeUnknown bool
}

func weightError(message string) error {
	return &campus.Error{Code: "WEIGHT_PLAN_INVALID", Message: message}
}

func weightProbability(bid, alpha float64) float64 {
	return 1 - math.Exp(-bid/(alpha+weightEpsilon))
}

func weightWaterfill(ids []string, utilities, alphas map[string]float64, budget, minimum int) map[string]float64 {
	result := map[string]float64{}
	if len(ids) == 0 {
		return result
	}
	remaining := float64(budget - minimum*len(ids))
	if remaining <= weightEpsilon {
		for _, id := range ids {
			result[id] = float64(minimum)
		}
		return result
	}
	high := 0.0
	for _, id := range ids {
		alpha := alphas[id] + weightEpsilon
		high = max(high, max(utilities[id], weightEpsilon)/(alpha*math.Exp(float64(minimum)/alpha)))
	}
	high *= 1.0001
	low := high * 1e-12
	for iteration := 0; iteration < 80; iteration++ {
		middle, extra := (low+high)/2, 0.0
		for _, id := range ids {
			alpha := alphas[id] + weightEpsilon
			extra += max(0, alpha*math.Log(max(utilities[id], weightEpsilon)/max(middle*alpha, weightEpsilon))-float64(minimum))
		}
		if extra > remaining {
			low = middle
		} else {
			high = middle
		}
	}
	total := 0.0
	for _, id := range ids {
		alpha := alphas[id] + weightEpsilon
		result[id] = float64(minimum) + max(0, alpha*math.Log(max(utilities[id], weightEpsilon)/max(high*alpha, weightEpsilon))-float64(minimum))
		total += result[id]
	}
	if difference := float64(budget) - total; math.Abs(difference) > 1e-6 {
		for _, id := range ids {
			result[id] = max(float64(minimum), result[id]+difference/float64(len(ids)))
		}
	}
	return result
}

func weightIntegerize(raw map[string]float64, budget, minimum, step int, utilities map[string]float64) map[string]int {
	result, order := map[string]int{}, []string{}
	remaining := budget
	for id, bid := range raw {
		result[id] = max(minimum, int(math.Floor((bid+weightEpsilon)/float64(step)))*step)
		remaining -= result[id]
		order = append(order, id)
	}
	sort.Slice(order, func(left, right int) bool {
		first, second := order[left], order[right]
		if raw[first]-float64(result[first]) != raw[second]-float64(result[second]) {
			return raw[first]-float64(result[first]) > raw[second]-float64(result[second])
		}
		if utilities[first] != utilities[second] {
			return utilities[first] > utilities[second]
		}
		return first < second
	})
	for index := 0; len(order) > 0 && remaining >= step; index++ {
		result[order[index%len(order)]] += step
		remaining -= step
	}
	return result
}

func OptimizeGroupedWeights(ctx context.Context, policy WeightPolicy, gradeSize int, market []WeightMarketCourse, candidates []WeightCandidate, groups []JWXKPlanGroup, conflicts [][2]string) (map[string]any, error) {
	if gradeSize < 1 || gradeSize > 100000 || policy.Minimum < 1 || policy.Budget < policy.Minimum || policy.Budget > 150 || policy.Step < 1 || policy.Minimum%policy.Step != 0 {
		return nil, weightError("年级人数、官方预算或投权步长无效")
	}
	if len(market) == 0 || len(candidates) == 0 || len(candidates) > 100 || len(groups) == 0 || len(groups) > 20 {
		return nil, weightError("请准备完整轮次市场数据和包含候选课程的方案组")
	}
	if policy.NodeLimit <= 0 {
		policy.NodeLimit = 200000
	}
	if policy.TimeLimit <= 0 {
		policy.TimeLimit = 1500 * time.Millisecond
	}
	groupTargets, marketByID := map[string]int{}, map[string]WeightMarketCourse{}
	marketOrder := []string{}
	for _, group := range groups {
		if group.ID == "" || group.Target < 1 || group.Target > 20 || groupTargets[group.ID] != 0 {
			return nil, weightError("方案组目标或标识无效")
		}
		groupTargets[group.ID] = group.Target
	}
	for _, course := range market {
		if course.ID == "" || course.Capacity <= 0 || course.Bidders < 0 {
			return nil, weightError("市场数据缺少有效容量或投注人数")
		}
		if _, exists := marketByID[course.ID]; !exists {
			marketOrder = append(marketOrder, course.ID)
		}
		marketByID[course.ID] = course
	}
	seen := map[string]bool{}
	for _, candidate := range candidates {
		if candidate.ID == "" || seen[candidate.ID] || len(candidate.Groups) == 0 || candidate.Utility < 1 || candidate.Utility > 10 || math.IsNaN(candidate.Utility) || math.IsInf(candidate.Utility, 0) || candidate.Capacity <= 0 || candidate.Bidders < 0 {
			return nil, weightError("候选课程重复或缺少有效意愿值、容量及投注人数")
		}
		seen[candidate.ID] = true
		memberships := map[string]bool{}
		for _, group := range candidate.Groups {
			if groupTargets[group] == 0 || memberships[group] {
				return nil, weightError("候选课程没有有效且唯一的方案组归属")
			}
			memberships[group] = true
		}
		if _, exists := marketByID[candidate.ID]; !exists {
			marketOrder = append(marketOrder, candidate.ID)
		}
		marketByID[candidate.ID] = WeightMarketCourse{candidate.ID, candidate.Capacity, candidate.Bidders}
	}
	totalBidders := 0.0
	for _, id := range marketOrder {
		totalBidders += float64(marketByID[id].Bidders)
	}
	averageRaw, maximum := totalBidders/float64(gradeSize), float64(policy.Budget)/float64(policy.Minimum)
	average := max(1, min(maximum, averageRaw))
	scopeMismatch := averageRaw > maximum+weightEpsilon
	entries := map[string]float64{}
	forecasts, alphas := map[string]map[string]float64{}, map[string]map[string]float64{}
	for index, multiplier := range []float64{0.8, 1, 1.6} {
		name := weightScenarios[index]
		entries[name] = max(1, min(maximum, average*multiplier))
		forecasts[name], alphas[name] = map[string]float64{}, map[string]float64{}
		addition := max(0, float64(gradeSize)*entries[name]-totalBidders)
		for _, id := range marketOrder {
			course := marketByID[id]
			extra := addition / float64(len(marketByID))
			if totalBidders > 0 {
				extra = addition * float64(course.Bidders) / totalBidders
			}
			forecasts[name][id] = min(float64(gradeSize), float64(course.Bidders)+extra)
		}
		for _, candidate := range candidates {
			alphas[name][candidate.ID] = float64(policy.Budget) / max(entries[name], weightEpsilon) * math.Log(max(forecasts[name][candidate.ID]/float64(candidate.Capacity), 1.05))
		}
	}
	safe, fixedIDs, counts := map[string]bool{}, map[string]bool{}, map[string]int{}
	utilities := map[string]float64{}
	selectable, fixedUtility := []WeightCandidate{}, 0.0
	for _, candidate := range candidates {
		safe[candidate.ID], utilities[candidate.ID] = candidate.Bidders < candidate.Capacity, candidate.Utility
		if candidate.AlreadySelected {
			fixedIDs[candidate.ID] = true
			fixedUtility += candidate.Utility
			for _, group := range candidate.Groups {
				counts[group]++
			}
		} else {
			selectable = append(selectable, candidate)
		}
	}
	sort.SliceStable(selectable, func(left, right int) bool {
		if selectable[left].Utility != selectable[right].Utility {
			return selectable[left].Utility > selectable[right].Utility
		}
		return selectable[left].ID < selectable[right].ID
	})
	pairs := map[[2]string]bool{}
	for _, pair := range conflicts {
		if pair[0] != pair[1] {
			pairs[pair], pairs[[2]string{pair[1], pair[0]}] = true, true
		}
	}
	allocate := func(selected []WeightCandidate) map[string]int {
		bids, competitive := map[string]int{}, []string{}
		remaining := policy.Budget
		for _, candidate := range selected {
			if safe[candidate.ID] {
				bids[candidate.ID] = policy.Minimum
				remaining -= policy.Minimum
			} else {
				competitive = append(competitive, candidate.ID)
			}
		}
		raw := weightWaterfill(competitive, utilities, alphas["neutral"], remaining, policy.Minimum)
		for id, bid := range weightIntegerize(raw, remaining, policy.Minimum, policy.Step, utilities) {
			bids[id] = bid
		}
		return bids
	}
	started, visited, approximate := time.Now(), 0, false
	var bestScore []float64
	var chosen []WeightCandidate
	var bestBids map[string]int
	var walk func(int, []WeightCandidate)
	walk = func(index int, selected []WeightCandidate) {
		visited++
		if visited > policy.NodeLimit || time.Since(started) > policy.TimeLimit || ctx.Err() != nil {
			approximate = true
			return
		}
		bids := allocate(selected)
		covered, utility, proxy, spent := 0, fixedUtility, 0.0, 0
		for _, group := range groups {
			covered += min(counts[group.ID], group.Target)
		}
		for _, candidate := range selected {
			utility += candidate.Utility
			probability := 1.0
			if !safe[candidate.ID] {
				probability = weightProbability(float64(bids[candidate.ID]), alphas["neutral"][candidate.ID])
			}
			proxy += candidate.Utility * probability
			spent += bids[candidate.ID]
		}
		score := []float64{float64(covered), utility, proxy, float64(-spent)}
		better := bestScore == nil
		if !better {
			for position, value := range score {
				if value != bestScore[position] {
					better = value > bestScore[position]
					break
				}
			}
		}
		if better {
			bestScore, bestBids, chosen = score, bids, append([]WeightCandidate{}, selected...)
		}
		if len(selected) >= policy.Budget/policy.Minimum {
			return
		}
		for next := index; next < len(selectable); next++ {
			candidate, blocked := selectable[next], false
			for _, group := range candidate.Groups {
				blocked = blocked || counts[group] >= groupTargets[group]
			}
			for id := range fixedIDs {
				blocked = blocked || pairs[[2]string{candidate.ID, id}]
			}
			for _, other := range selected {
				blocked = blocked || pairs[[2]string{candidate.ID, other.ID}]
			}
			if blocked {
				continue
			}
			for _, group := range candidate.Groups {
				counts[group]++
			}
			walk(next+1, append(selected, candidate))
			for _, group := range candidate.Groups {
				counts[group]--
			}
			if approximate {
				return
			}
		}
	}
	walk(0, nil)
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if bestScore == nil {
		return nil, weightError("当前预算和冲突约束下不存在可行投权方案")
	}
	chosenIDs, spent := map[string]bool{}, 0
	for _, candidate := range chosen {
		chosenIDs[candidate.ID] = true
		spent += bestBids[candidate.ID]
		for _, group := range candidate.Groups {
			counts[group]++
		}
	}
	courseResults, groupResults := []map[string]any{}, []map[string]any{}
	unknown := false
	for _, candidate := range candidates {
		unknown = unknown || candidate.TimeUnknown
		selected, bid := candidate.AlreadySelected || chosenIDs[candidate.ID], bestBids[candidate.ID]
		forecast, rates := map[string]float64{}, map[string]float64{}
		classification, reason := "OUT", "该课程未进入满足方案组目标的本轮推荐组合"
		if candidate.AlreadySelected {
			classification, reason = "SELECTED", "该课程已经形成选课结果，不参与本轮投权分配"
		} else if selected && safe[candidate.ID] {
			classification, reason = "SAFE", fmt.Sprintf("当前 %d/%d 尚未满，实时策略将其视为 SAFE，固定使用官方最低投权 %d 点；终局预测仅作风险参考", candidate.Bidders, candidate.Capacity, policy.Minimum)
		} else if selected {
			classification, reason = "COMP", fmt.Sprintf("当前 %d/%d，中性情景预计 %.1f/%d；water-filling 结合意愿评分分配 %d 点", candidate.Bidders, candidate.Capacity, forecasts["neutral"][candidate.ID], candidate.Capacity, bid)
		}
		flat, firstForecast := true, math.Round(forecasts["conservative"][candidate.ID]*1e6)
		for _, name := range weightScenarios {
			forecast[name] = forecasts[name][candidate.ID]
			flat = flat && math.Round(forecast[name]*1e6) == firstForecast
			rates[name] = 0
			if classification == "SELECTED" || classification == "SAFE" {
				rates[name] = 1
			} else if classification == "COMP" {
				rates[name] = weightProbability(float64(bid), alphas[name][candidate.ID])
			}
		}
		forecastStatus := "available"
		if flat {
			forecastStatus = "flat_current"
		}
		if scopeMismatch {
			forecastStatus = "scope_mismatch"
		}
		blocked := []string{}
		if !selected {
			for _, other := range candidates {
				if (fixedIDs[other.ID] || chosenIDs[other.ID]) && pairs[[2]string{candidate.ID, other.ID}] {
					blocked = append(blocked, other.ID)
				}
			}
		}
		courseResults = append(courseResults, map[string]any{"course_id": candidate.ID, "name": candidate.Name, "bid": bid, "utility": candidate.Utility, "classification": classification, "selected": selected, "already_selected": candidate.AlreadySelected, "scenario_success_rates": rates, "forecast_participants": forecast, "forecast_status": forecastStatus, "recommendation_reason": reason, "blocked_by_conflicts": blocked, "time_unknown": candidate.TimeUnknown})
	}
	missing := false
	for _, group := range groups {
		missing = missing || counts[group.ID] < group.Target
		groupResults = append(groupResults, map[string]any{"group_id": group.ID, "name": group.Name, "target_count": group.Target, "selected_count": counts[group.ID], "missing_count": max(0, group.Target-counts[group.ID]), "satisfied": counts[group.ID] >= group.Target})
	}
	warnings := []string{}
	if averageRaw < 1-weightEpsilon {
		warnings = append(warnings, "全市场已投注次数低于年级人数，终局预测的置信度较低")
	}
	if scopeMismatch {
		warnings = append(warnings, "当前轮次累计投注次数超出年级人数模型的理论范围，三种终局人数情景暂不可区分；实时 SAFE/COMP 分类和当前人数仍然有效")
	}
	if unknown {
		warnings = append(warnings, "部分课程时间信息待核验，模型没有将其视为无冲突")
	}
	if missing {
		warnings = append(warnings, "当前预算、冲突或候选范围无法满足全部方案组目标")
	}
	if approximate {
		warnings = append(warnings, "候选组合较多，已返回搜索时限内找到的最佳可行方案")
	}
	return map[string]any{"model_version": WeightModelVersion, "market_confidence": "uncalibrated_proxy", "approximate": approximate, "nodes_visited": visited, "budget_used": spent, "courses": courseResults, "groups": groupResults, "warnings": warnings, "diagnostics": map[string]any{"grade_size": gradeSize, "market_course_count": len(marketByID), "total_current_bidders": totalBidders, "average_entries_raw": averageRaw, "market_scope_mismatch": scopeMismatch, "scenario_entries": entries}}, nil
}
