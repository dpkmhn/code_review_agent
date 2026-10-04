import React from 'react';

// Git-flow illustration: a feature branch opens a pull request, the agent reviews it, then it merges.
export default function ReviewJourney() {
  return <figure className="review-journey">
    <figcaption><span>THE GIT FLOW</span><span className="journey-caption">Where the agent steps in</span></figcaption>
    <svg viewBox="0 0 520 270" role="img" aria-label="Git flow illustration: a feature branch leaves main, gathers commits, opens a pull request that the agent reviews, then merges back into main. This is an overview, not live progress.">
      <defs>
        <pattern id="journey-grid" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".7" fill="#667498" opacity=".2" /></pattern>
        <linearGradient id="journey-pr" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#7c53d9" /><stop offset="1" stopColor="#b06ad8" /></linearGradient>
      </defs>
      <rect width="520" height="270" fill="url(#journey-grid)" />
      <text x="34" y="44" className="journey-kicker">main</text>
      <path className="flow-main" d="M40 64 H490" />
      <path className="flow-feature" d="M110 64 C142 64 144 124 176 124 H384 C416 124 418 64 450 64" />
      <text x="184" y="112" className="journey-kicker">feature/fix-auth</text>
      <path className="flow-pr-link" d="M360 130 V176" />
      {[40, 110, 450, 490].map((x) => <circle key={x} className="flow-dot main" cx={x} cy="64" r="7" />)}
      {[[220, 'commit'], [290, 'commit'], [360, 'commit']].map(([x, label], index) =>
        <g key={x} className="flow-commit" style={{ animationDelay: `${index * 0.35}s` }}>
          <circle className="flow-dot feature" cx={x} cy="124" r="6" />
          <text x={x} y="148" textAnchor="middle" className="journey-kicker">{label}</text>
        </g>)}
      <text x="450" y="44" textAnchor="middle" className="journey-kicker">merge</text>
      <g className="flow-pr">
        <rect x="298" y="176" width="124" height="38" rx="10" />
        <text x="360" y="200" textAnchor="middle">Pull request</text>
      </g>
      <g className="flow-agents">
        {[['#f2a46b', 334, 'bug'], ['#9aa8ff', 360, 'ctx'], ['#f08bb0', 386, 'sec']].map(([color, x, key], index) =>
          <circle key={key} cx={x} cy="236" r="7" fill="none" stroke={color} strokeWidth="1.6" style={{ animationDelay: `${index * 0.4}s` }} />)}
      </g>
      <text x="360" y="262" textAnchor="middle" className="journey-note">The agent reviews here, before the merge.</text>
      <circle className="flow-pulse" r="4" cx="0" cy="0"><animateMotion dur="6s" repeatCount="indefinite" path="M110 64 C142 64 144 124 176 124 H384 C416 124 418 64 450 64" /></circle>
    </svg>
    <div className="journey-legend" aria-hidden="true"><span><i className="main" />main branch</span><span><i className="feature" />feature branch</span><span><i className="pr" />pull request</span></div>
  </figure>;
}
