import {reasonLabel, type Analysis, type Presentation} from './types';

/** Keep veto identifiers and their frozen audit checks together at the UI boundary. */
export function MTFEntryBandVetoes({analysis,presentation}: {analysis:Analysis;presentation:Presentation}) {
  return <>{analysis.entry_candidate?.vetoes.map(v=>
    <p key={v}>Veto: {reasonLabel(v,presentation,analysis.risk_audit?.checks)}.</p>)}</>;
}
