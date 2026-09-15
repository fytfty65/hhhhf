import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(figsize=(13.5, 9.5), dpi=300)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis('off')

# 颜色定义
C_Q1_BG, C_Q1_BAR, C_Q1_TAG = '#E8F5E9', '#C6E0B4', '#548235'
C_Q2_BG, C_Q2_BAR, C_Q2_TAG = '#EBF3FA', '#BDD7EE', '#2E75B6'
C_Q3_BG, C_Q3_BAR, C_Q3_TAG = '#FDF2F2', '#F8CBCB', '#C55A5A'
C_Q4_BG, C_Q4_BAR, C_Q4_TAG = '#FDF0ED', '#F4C7C3', '#C00000'

def draw_card(x, y, w, h, bg, bar, tag_color, title):
    """绘制带顶栏的大面板"""
    card = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1.0",
                                  facecolor=bg, edgecolor=tag_color, linewidth=1.2)
    ax.add_patch(card)
    header = patches.FancyBboxPatch((x, y + h - 5.5), w, 5.5, boxstyle="round,pad=0.1,rounding_size=0.8",
                                    facecolor=bar, edgecolor=tag_color, linewidth=0.8)
    ax.add_patch(header)
    ax.text(x + w/2, y + h - 2.8, title, ha='center', va='center', fontsize=12, fontweight='bold')

def draw_box(x, y, w, h, text, fs=8.5, fw='normal', tc='black', bg='white', border='#A6A6A6'):
    """绘制子卡片"""
    box = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1,rounding_size=0.5",
                                 facecolor=bg, edgecolor=border, linewidth=0.8)
    ax.add_patch(box)
    if text:
        ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=fs,
                fontweight=fw, color=tc, multialignment='center')

def draw_tag(x, y, w, h, text, color):
    """绘制垂直侧边标签"""
    tag = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1,rounding_size=0.4",
                                 facecolor=color, edgecolor='none')
    ax.add_patch(tag)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center', fontsize=9,
            fontweight='bold', color='white', multialignment='center')

# 标题
ax.text(50, 96.5, '微网面向不确定外部购电的储能滚动调控策略建模总分析', ha='center', fontsize=17, fontweight='bold')

# ----------------- 问题一 -----------------
draw_card(2, 50, 46, 42, C_Q1_BG, C_Q1_BAR, C_Q1_TAG, '问题一：基于已知预测的储能最优调度')
draw_tag(3, 80.5, 6, 5.5, '数据\n预处理', C_Q1_TAG)
draw_tag(3, 62, 6, 17, '模型\n核心', C_Q1_TAG)
draw_tag(3, 52, 6, 8.5, '结果\n验证', C_Q1_TAG)

draw_box(10.5, 80.5, 36, 5.5, '输入数据：分时电价，负荷，光伏预测，\n储能参数，ESS Params', fs=8.5)
draw_box(10.5, 74.5, 17, 4.5, '十分钟能量平衡')
draw_box(10.5, 69.5, 17, 4.5, 'SOC状态递推')
draw_box(10.5, 64.5, 17, 4.5, '全日线性规划')
draw_box(10.5, 59.5, 17, 4.5, '目标：最小购电成本', fw='bold')

# 问题一 SOC 曲线框
draw_box(29, 59.5, 17.5, 19.5, '')
ax.text(37.75, 76.5, '模型核心', ha='center', fontsize=8.5, fontweight='bold')
ax.text(37.75, 73.5, '目标：最小购电成本', ha='center', fontsize=7.5)
soc_x = np.linspace(30.5, 45, 12)
soc_y = [63, 68, 71, 71, 65, 61, 64, 71, 71, 61, 61, 63]
ax.plot(soc_x, soc_y, color='#2E7D32', lw=1.8)
ax.text(30.5, 61, '1200', fontsize=6, ha='right')
ax.text(30.5, 71, '10800', fontsize=6, ha='right')

draw_box(10.5, 52, 17.5, 8.5, '日成本 35,126.95 元', fs=9)
draw_box(29, 52, 17.5, 8.5, '降本 26.90%', fs=9, fw='bold')

# ----------------- 问题二 -----------------
draw_card(52, 50, 46, 42, C_Q2_BG, C_Q2_BAR, C_Q2_TAG, '问题二：基于历史日前预测的全年回测')
draw_tag(53, 80.5, 4.5, 5.5, '日前\n预测', C_Q2_TAG)
draw_tag(53, 67, 4.5, 12, '模型\n构建', C_Q2_TAG)
draw_tag(53, 52, 4.5, 13.5, '全年\n回测', C_Q2_TAG)

draw_box(59, 80.5, 37.5, 5.5, '七日中位数预测器', fs=9.5)
draw_box(65, 73, 26, 4.8, '0:00日前优化调度')
draw_box(65, 67, 26, 4.8, '冻结计划执行')
ax.annotate('', xy=(78, 67), xytext=(78, 73), arrowprops=dict(arrowstyle="->", color="gray"))

draw_box(59, 52.5, 8, 13, '334\n日历史\n序列', fs=8.5)
ax.text(71, 59, '全年\n总成本\n统计', ha='center', va='center', fontsize=8.5)
ax.annotate('', xy=(74, 59), xytext=(67.5, 59), arrowprops=dict(arrowstyle="->", color="gray"))
draw_box(74.5, 52.5, 22, 13, 'Q2有储能: 17.907 百万元\nQ2无储能: 21.217 百万元\n储能收益: 15.60%\n⚠ 五倍价紧急购电风险',
         fs=8, tc='#C00000', border='#C00000')

# ----------------- 问题三 -----------------
draw_card(2, 4, 46, 42, C_Q3_BG, C_Q3_BAR, C_Q3_TAG, '问题三：基于滚动预测更新与调整结算')
draw_tag(3, 23, 4, 18.5, '多\n升\n更\n点', C_Q3_TAG)
draw_tag(3, 12, 6, 9.5, '成本\n分解与\n结算', C_Q3_TAG)
draw_tag(3, 5, 6, 6, '滚动\n降证', C_Q3_TAG)

steps = [('0:00初始计划', 36.5), ('6:00/12:00更新时点', 30.5), ('18:00补充更新', 24.5)]
for label, y_pos in steps:
    draw_box(8.5, y_pos, 13, 4.5, label, fs=8)
    draw_box(23, y_pos, 10.5, 4.5, '插值光伏预测', fs=7.5)
    draw_box(35, y_pos, 11.5, 4.5, '剩余时域再优化', fs=7.5)
    ax.annotate('', xy=(23, y_pos+2.25), xytext=(21.5, y_pos+2.25), arrowprops=dict(arrowstyle="->", color="gray"))
    ax.annotate('', xy=(35, y_pos+2.25), xytext=(33.5, y_pos+2.25), arrowprops=dict(arrowstyle="->", color="gray"))

draw_box(10.5, 12.5, 11, 8.5, '下调-50%返还', fs=8)
draw_box(22.5, 12.5, 11, 8.5, '上调+150%计费', fs=8)
draw_box(34.5, 12.5, 12, 8.5, '5倍价紧急购电', fs=8)

draw_box(10.5, 5, 36, 6, '滚动降本 0.609 百万元\n消融实验：6/12点贡献 99.77%', fs=8.5, fw='bold')

# ----------------- 问题四 -----------------
draw_card(52, 4, 46, 42, C_Q4_BG, C_Q4_BAR, C_Q4_TAG, '问题四：动态电价下的策略迁移')
draw_tag(53, 35, 4, 6.5, '数\n据', C_Q4_TAG)
draw_tag(53, 23.5, 4, 10, '结\n据', C_Q4_TAG)
draw_tag(53, 5, 4, 17, '结\n果\n对\n比', C_Q4_TAG)

draw_box(59, 36, 37.5, 5.5, '十分钟动态电价（附件 4）', fs=9.5)
draw_box(58.5, 28, 12, 4.5, '日前计划迁移', fs=8)
draw_box(73, 25.5, 12, 4.5, '滚动策略迁移', fs=8)
draw_box(86.5, 29.5, 10.5, 4.5, '再运行化优化', fs=7.5)
draw_box(86.5, 24, 10.5, 4.5, '剩余时域再优化', fs=7.5)

ax.annotate('', xy=(64.5, 32.5), xytext=(64.5, 36), arrowprops=dict(arrowstyle="->", color="gray"))
ax.annotate('', xy=(73, 27.75), xytext=(70.5, 29), arrowprops=dict(arrowstyle="->", color="gray"))
ax.annotate('', xy=(86.5, 31.75), xytext=(85, 27.75), arrowprops=dict(arrowstyle="->", color="gray"))
ax.annotate('', xy=(91.75, 28.5), xytext=(91.75, 29.5), arrowprops=dict(arrowstyle="->", color="gray"))

draw_box(58.5, 17, 9.5, 5, '日前CV1...21\n日比较结本', fs=7)
draw_box(69, 16.5, 27.5, 6, 'Q4-2动态价前计划：18.397 百万元\nQ4-3滚动调控：17.913 百万元', fs=8, fw='bold')

# 正态分布示意图
draw_box(58.5, 5, 15, 10, '')
dist_x = np.linspace(60, 72, 50)
dist_y1 = np.exp(-0.5 * ((dist_x - 64) / 1.5)**2) * 6 + 6
dist_y2 = np.exp(-0.5 * ((dist_x - 65) / 2.5)**2) * 4.5 + 6
ax.plot(dist_x, dist_y1, color='#0097A7', lw=1.5)
ax.plot(dist_x, dist_y2, color='#E64A19', lw=1.5)

draw_box(74.5, 5, 22, 10, 'Q4下滚动调控降本 3.56%\n日成本 CV 上升至 31.55%\n运营建议：0/6/12点更新组合', fs=8)

# ----------------- 递进流转大箭头 -----------------
# Q1 -> Q2
ax.annotate('', xy=(52, 71), xytext=(48, 71), arrowprops=dict(arrowstyle="->", lw=4, color=C_Q1_TAG, alpha=0.7))
# Q2 -> Q3 (转弯折线)
ax.plot([75, 75, 25, 25], [50, 48, 48, 46], color=C_Q2_TAG, lw=2.5, alpha=0.7)
ax.annotate('', xy=(25, 46), xytext=(25, 46.5), arrowprops=dict(arrowstyle="->", lw=2.5, color=C_Q2_TAG, alpha=0.7))
# Q3 -> Q4
ax.annotate('', xy=(52, 25), xytext=(48, 25), arrowprops=dict(arrowstyle="->", lw=4, color=C_Q3_TAG, alpha=0.7))

ax.text(50, 0.8, '图 2  研究及模组的结构架构图', ha='center', fontsize=11, fontweight='bold')

plt.tight_layout()
plt.show()