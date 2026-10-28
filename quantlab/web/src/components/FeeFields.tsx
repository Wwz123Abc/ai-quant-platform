import type { FeePlan } from "../lib/fees";
import { Field } from "./ui";

type Change = (e: { target: { value: string } }) => void;

/** The fee plan select, with a dollars-per-order input when the plan is a flat fee. */
export default function FeeFields({ plan, flat, onPlan, onFlat }: { plan: FeePlan; flat: string; onPlan: Change; onFlat: Change }) {
  return (
    <>
      <Field label="收费方式">
        <select value={plan} onChange={onPlan}>
          <option value="trade25">汇丰 Trade25</option>
          <option value="standard">汇丰标准收费</option>
          <option value="flat">每笔固定金额</option>
        </select>
      </Field>
      {plan === "flat" ? (
        <Field label="每笔手续费（$）"><input type="number" min={0} step="any" value={flat} onChange={onFlat} /></Field>
      ) : null}
    </>
  );
}
