export type FeePlan = "trade25" | "standard" | "flat";

/** What a form sends as fee: an HSBC plan, or dollars per order. */
export function feeValue(plan: FeePlan, flat: string): "trade25" | "standard" | number {
  return plan === "flat" ? Number(flat) : plan;
}

/** An account's fee setting in words. */
export function feeLabel(fee: string | number): string {
  if (fee === "trade25") return "汇丰 Trade25";
  if (fee === "standard") return "汇丰标准收费";
  return `每笔 $${fee}`;
}

export const FEE_NOTE =
  "汇丰 Trade25：当月买卖合计不超过 25 万港元（约 $32,000）免佣金，超出后每笔 $18；标准收费：每笔 $18，超过 1,000 股的部分每股加 $0.015。" +
  "两种都在卖出时另收 SEC 费（成交额的 0.00206%）。Trade25 每月 25 港元的账户费不论用不用这个策略都要交，不计入；月成交只算这里的买卖，不含你在汇丰的其他交易。";
