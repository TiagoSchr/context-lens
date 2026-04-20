"""Generate real token savings chart from proof_savings.py data."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# Real data from proof_savings.py run against the actual context-lens project
queries = [
    "explain MCP\nserver calls",
    "fix walker\nsymlinks bug",
    "find search_symbols\ncallers",
]
with_lens   = [5514, 6859, 5666]
without_lens = [264967, 264967, 264967]
savings_pct  = [97.9, 97.4, 97.9]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))
fig.patch.set_facecolor('#0d1117')
for ax in (ax1, ax2):
    ax.set_facecolor('#161b22')
    ax.spines[:].set_color('#30363d')
    ax.tick_params(colors='#8b949e')
    ax.xaxis.label.set_color('#8b949e')
    ax.yaxis.label.set_color('#8b949e')
    ax.title.set_color('#e6edf3')

# Bar chart: tokens comparison
x = np.arange(len(queries))
w = 0.35
bars1 = ax1.bar(x - w/2, [t/1000 for t in without_lens], w,
                label='Without @lens', color='#da3633', alpha=0.85, zorder=3)
bars2 = ax1.bar(x + w/2, [t/1000 for t in with_lens], w,
                label='With @lens', color='#238636', alpha=0.85, zorder=3)

ax1.set_xticks(x)
ax1.set_xticklabels(queries, fontsize=9, color='#c9d1d9')
ax1.set_ylabel('Tokens (thousands)', color='#8b949e')
ax1.set_title('Tokens sent to AI per query\n(real index: 123 files, 264,967 tokens)', fontsize=10)
ax1.legend(facecolor='#21262d', labelcolor='#c9d1d9', edgecolor='#30363d', fontsize=9)
ax1.grid(axis='y', color='#30363d', zorder=0)
ax1.set_ylim(0, 300)

for bar in bars1:
    ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 3,
             f'{bar.get_height():.0f}k', ha='center', va='bottom',
             fontsize=8, color='#da3633')
for bar, pct, raw in zip(bars2, savings_pct, with_lens):
    h = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2, h + 3,
             f'{raw:,}', ha='center', va='bottom',
             fontsize=7, color='#3fb950')

# Savings % horizontal bars
bars3 = ax2.barh(queries, savings_pct, color='#3fb950', alpha=0.85, height=0.4, zorder=3)
ax2.set_xlim(0, 105)
ax2.set_xlabel('Token savings (%)', color='#8b949e')
ax2.set_title('Token savings per query\n(measured against all 123 indexed files)', fontsize=10)
ax2.grid(axis='x', color='#30363d', zorder=0)
ax2.tick_params(axis='y', labelsize=9, colors='#c9d1d9')
for bar, pct in zip(bars3, savings_pct):
    ax2.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
             f'{pct}%', va='center', fontsize=11, color='#3fb950', fontweight='bold')

avg = sum(savings_pct) / len(savings_pct)
ax2.axvline(avg, color='#f78166', linestyle='--', linewidth=1.2, alpha=0.7)
ax2.text(avg + 0.5, -0.55, f'avg {avg:.1f}%', color='#f78166', fontsize=9)

fig.suptitle('Context Lens — Real Token Savings', fontsize=13, color='#e6edf3', y=1.01)
fig.text(0.5, -0.02,
         'Source: bench/proof_savings.py  |  Project: context-lens (123 files / 264,967 total tokens)',
         ha='center', fontsize=8, color='#8b949e')

plt.tight_layout()
out = Path(__file__).parent.parent / 'docs' / 'token_savings.png'
out.parent.mkdir(exist_ok=True)
plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='#0d1117')
print(f"Saved: {out}")
