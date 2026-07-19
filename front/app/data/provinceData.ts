// 文件路径: src/data/provinceData.ts

export interface ProvinceDataInfo {
  summary: {
    tags: string[];
    score: string;
    hotCount: number;
    poyPreviews: string[];
  };
  hotspots: {
    name: string;
    type: 'food' | 'scenic';
    lnglat: [number, number];
    desc: string;
    recommend: string;
  }[];
}

export const PROVINCE_DATA: Record<string, ProvinceDataInfo> = {
  "北京市": {
    "summary": { "tags": ["皇城根", "首都", "长城"], "score": "9.6", "hotCount": 6, "poyPreviews": ["故宫博物院", "八达岭长城", "天安门广场", "颐和园"] },
    "hotspots": [
      { "name": "故宫博物院", "type": "scenic", "lnglat": [116.3970, 39.9181], "desc": "明清两代皇家宫殿，中国古代宫廷建筑之精华", "recommend": "核心必游体验" },
      { "name": "八达岭长城", "type": "scenic", "lnglat": [116.0178, 40.3547], "desc": "不到长城非好汉，最壮观的明长城段", "recommend": "核心必游体验" },
      { "name": "天安门广场", "type": "scenic", "lnglat": [116.3975, 39.9087], "desc": "共和国的心脏，见证无数历史时刻", "recommend": "核心必游体验" },
      { "name": "颐和园", "type": "scenic", "lnglat": [116.2787, 39.9969], "desc": "中国古典园林之首", "recommend": "核心必游体验" }
    ]
  },
  "天津市": {
    "summary": { "tags": ["相声", "港口", "津门"], "score": "8.8", "hotCount": 3, "poyPreviews": ["天津之眼", "古文化街", "盘山", "狗不理包子"] },
    "hotspots": [
      { "name": "天津之眼", "type": "scenic", "lnglat": [117.1932, 39.1551], "desc": "跨越海河的摩天轮，夜景绝佳", "recommend": "当地热门地标" },
      { "name": "古文化街", "type": "scenic", "lnglat": [117.1921, 39.1437], "desc": "体验纯正津味文化的好去处", "recommend": "当地热门地标" },
      { "name": "盘山", "type": "scenic", "lnglat": [117.3785, 40.0917], "desc": "早知有盘山，何必下江南", "recommend": "当地热门地标" },
      { "name": "狗不理包子", "type": "food", "lnglat": [117.2043, 39.1312], "desc": "百年津门老字号美食", "recommend": "经典特色寻味" }
    ]
  },
  "上海市": {
    "summary": { "tags": ["魔都", "金融", "外滩"], "score": "9.7", "hotCount": 5, "poyPreviews": ["东方明珠", "外滩", "上海迪士尼乐园", "豫园"] },
    "hotspots": [
      { "name": "东方明珠", "type": "scenic", "lnglat": [121.4997, 31.2398], "desc": "浦东陆家嘴的标志性建筑", "recommend": "城市天际线" },
      { "name": "外滩", "type": "scenic", "lnglat": [121.4905, 31.2403], "desc": "万国建筑博览群，魔都风华", "recommend": "城市天际线" },
      { "name": "上海迪士尼乐园", "type": "scenic", "lnglat": [121.6608, 31.1453], "desc": "点亮心中奇梦的主题乐园", "recommend": "城市天际线" },
      { "name": "豫园", "type": "scenic", "lnglat": [121.4914, 31.2297], "desc": "江南古典园林，城隍庙旁的好去处", "recommend": "城市天际线" }
    ]
  },
  "重庆市": {
    "summary": { "tags": ["山城", "火锅", "轻轨"], "score": "9.3", "hotCount": 5, "poyPreviews": ["洪崖洞", "磁器口古镇", "武隆天生三桥", "解放碑步行街"] },
    "hotspots": [
      { "name": "洪崖洞", "type": "scenic", "lnglat": [106.5826, 29.5657], "desc": "现实版千与千寻，梦幻夜景", "recommend": "8D魔幻体验" },
      { "name": "磁器口古镇", "type": "scenic", "lnglat": [106.4478, 29.5833], "desc": "品尝陈麻花，体验老重庆慢生活", "recommend": "8D魔幻体验" },
      { "name": "武隆天生三桥", "type": "scenic", "lnglat": [107.7982, 29.4387], "desc": "大自然的鬼斧神工，变形金刚取景地", "recommend": "8D魔幻体验" },
      { "name": "解放碑步行街", "type": "scenic", "lnglat": [106.5827, 29.5617], "desc": "重庆繁华核心，打望山城美女", "recommend": "8D魔幻体验" }
    ]
  },
  "河北省": {
    "summary": { "tags": ["承德避暑山庄", "北戴河", "燕赵"], "score": "8.7", "hotCount": 3, "poyPreviews": ["山海关", "承德避暑山庄", "北戴河", "白洋淀"] },
    "hotspots": [
      { "name": "山海关", "type": "scenic", "lnglat": [119.7932, 39.9982], "desc": "天下第一关，长城入海处", "recommend": "历史人文之旅" },
      { "name": "承德避暑山庄", "type": "scenic", "lnglat": [117.9366, 40.9873], "desc": "清代皇家夏宫，古典园林杰作", "recommend": "历史人文之旅" },
      { "name": "北戴河", "type": "scenic", "lnglat": [119.5207, 39.8242], "desc": "老牌海滨避暑胜地", "recommend": "历史人文之旅" },
      { "name": "白洋淀", "type": "scenic", "lnglat": [116.0013, 38.8736], "desc": "华北明珠，芦苇荡里泛舟", "recommend": "历史人文之旅" }
    ]
  },
  "山西省": {
    "summary": { "tags": ["云冈石窟", "平遥古城", "煤都"], "score": "9.0", "hotCount": 4, "poyPreviews": ["云冈石窟", "平遥古城", "五台山", "壶口瀑布"] },
    "hotspots": [
      { "name": "云冈石窟", "type": "scenic", "lnglat": [113.1338, 40.1125], "desc": "中国佛教艺术的巅峰之作", "recommend": "三晋大地必游" },
      { "name": "平遥古城", "type": "scenic", "lnglat": [112.1856, 37.2019], "desc": "保存最完好的明清古城之一", "recommend": "三晋大地必游" },
      { "name": "五台山", "type": "scenic", "lnglat": [113.5912, 39.0107], "desc": "四大佛教名山之首，文殊菩萨道场", "recommend": "三晋大地必游" },
      { "name": "壶口瀑布", "type": "scenic", "lnglat": [110.4469, 36.1474], "desc": "千里黄河一壶收，气势磅礴", "recommend": "三晋大地必游" }
    ]
  },
  "内蒙古自治区": {
    "summary": { "tags": ["草原", "那达慕", "骑马"], "score": "8.5", "hotCount": 3, "poyPreviews": ["呼伦贝尔大草原", "响沙湾", "成吉思汗陵", "额济纳胡杨林"] },
    "hotspots": [
      { "name": "呼伦贝尔大草原", "type": "scenic", "lnglat": [119.7355, 49.2119], "desc": "风吹草低见牛羊的极致风光", "recommend": "塞外风情体验" },
      { "name": "响沙湾", "type": "scenic", "lnglat": [109.8932, 40.2684], "desc": "沙漠中的迪士尼，体验滑沙乐趣", "recommend": "塞外风情体验" },
      { "name": "成吉思汗陵", "type": "scenic", "lnglat": [109.8477, 39.3544], "desc": "一代天骄的安息之地", "recommend": "塞外风情体验" },
      { "name": "额济纳胡杨林", "type": "scenic", "lnglat": [101.0661, 41.9592], "desc": "三千年不死不倒不朽的金色童话", "recommend": "塞外风情体验" }
    ]
  },
  "辽宁省": {
    "summary": { "tags": ["沈阳故宫", "大连", "工业"], "score": "8.6", "hotCount": 3, "poyPreviews": ["沈阳故宫", "大连老虎滩", "本溪水洞", "千山"] },
    "hotspots": [
      { "name": "沈阳故宫", "type": "scenic", "lnglat": [123.4555, 41.7998], "desc": "清代入关前的皇家宫殿", "recommend": "东北特色地标" },
      { "name": "大连老虎滩海洋公园", "type": "scenic", "lnglat": [121.6812, 38.8752], "desc": "大连老牌海洋主题乐园", "recommend": "东北特色地标" },
      { "name": "本溪水洞", "type": "scenic", "lnglat": [124.0831, 41.2974], "desc": "世界最长的充水溶洞", "recommend": "东北特色地标" },
      { "name": "千山", "type": "scenic", "lnglat": [123.1289, 41.0127], "desc": "东北明珠，佛教名山", "recommend": "东北特色地标" }
    ]
  },
  "吉林省": {
    "summary": { "tags": ["长白山", "雾凇", "天池"], "score": "9.0", "hotCount": 4, "poyPreviews": ["长白山天池", "吉林雾凇岛", "伪满皇宫", "长春净月潭"] },
    "hotspots": [
      { "name": "长白山天池", "type": "scenic", "lnglat": [128.0556, 42.0088], "desc": "中朝界湖，神秘壮观的火山口湖", "recommend": "北国风光绝佳" },
      { "name": "吉林雾凇岛", "type": "scenic", "lnglat": [126.4940, 43.9353], "desc": "中国四大自然奇观之一的雾凇盛景", "recommend": "北国风光绝佳" },
      { "name": "伪满皇宫博物院", "type": "scenic", "lnglat": [125.3523, 43.8973], "desc": "末代皇帝溥仪的宫殿，历史的见证", "recommend": "北国风光绝佳" },
      { "name": "长春净月潭", "type": "scenic", "lnglat": [125.4667, 43.7892], "desc": "亚洲最大的人工林海", "recommend": "北国风光绝佳" }
    ]
  },
  "黑龙江省": {
    "summary": { "tags": ["冰雪", "漠河", "哈尔滨"], "score": "9.4", "hotCount": 5, "poyPreviews": ["冰雪大世界", "五大连池", "漠河北极村", "镜泊湖"] },
    "hotspots": [
      { "name": "哈尔滨冰雪大世界", "type": "scenic", "lnglat": [126.5714, 45.7796], "desc": "每年冬季的冰雪奇缘，流光溢彩", "recommend": "极致冰雪体验" },
      { "name": "五大连池", "type": "scenic", "lnglat": [126.2399, 48.7169], "desc": "壮观的火山地质公园与冷泉", "recommend": "极致冰雪体验" },
      { "name": "漠河北极村", "type": "scenic", "lnglat": [122.3599, 53.4848], "desc": "中国最北端，寻找极光的地方", "recommend": "极致冰雪体验" },
      { "name": "镜泊湖", "type": "scenic", "lnglat": [128.9742, 43.7842], "desc": "高山堰塞湖，冬捕奇观", "recommend": "极致冰雪体验" }
    ]
  },
  "江苏省": {
    "summary": { "tags": ["园林", "太湖", "金陵"], "score": "9.5", "hotCount": 5, "poyPreviews": ["苏州园林", "南京夫子庙", "无锡鼋头渚", "周庄古镇"] },
    "hotspots": [
      { "name": "苏州园林", "type": "scenic", "lnglat": [120.6321, 31.3176], "desc": "江南园林甲天下", "recommend": "江南水乡漫游" },
      { "name": "南京夫子庙", "type": "scenic", "lnglat": [118.7936, 32.0224], "desc": "秦淮河畔，金陵文脉", "recommend": "江南水乡漫游" },
      { "name": "无锡太湖鼋头渚", "type": "scenic", "lnglat": [120.2241, 31.5261], "desc": "太湖佳绝处，赏樱胜地", "recommend": "江南水乡漫游" },
      { "name": "周庄古镇", "type": "scenic", "lnglat": [120.8535, 31.1403], "desc": "中国第一水乡，夜泊周庄", "recommend": "江南水乡漫游" }
    ]
  },
  "浙江省": {
    "summary": { "tags": ["西湖", "丝绸", "龙井"], "score": "9.6", "hotCount": 6, "poyPreviews": ["杭州西湖", "乌镇", "普陀山", "雁荡山"] },
    "hotspots": [
      { "name": "杭州西湖", "type": "scenic", "lnglat": [120.1577, 30.2589], "desc": "人间天堂，诗画江南的代表", "recommend": "诗画浙江打卡" },
      { "name": "乌镇", "type": "scenic", "lnglat": [120.4948, 30.7486], "desc": "水阁人家，梦里的枕水小镇", "recommend": "诗画浙江打卡" },
      { "name": "普陀山", "type": "scenic", "lnglat": [122.3909, 29.9866], "desc": "海天佛国，观音菩萨道场", "recommend": "诗画浙江打卡" },
      { "name": "雁荡山", "type": "scenic", "lnglat": [121.0650, 28.3670], "desc": "东南第一山，奇峰怪石", "recommend": "诗画浙江打卡" }
    ]
  },
  "安徽省": {
    "summary": { "tags": ["黄山", "徽派建筑", "黄梅戏"], "score": "9.3", "hotCount": 5, "poyPreviews": ["黄山风景区", "宏村", "九华山", "西递"] },
    "hotspots": [
      { "name": "黄山风景区", "type": "scenic", "lnglat": [118.1878, 30.0916], "desc": "五岳归来不看山，黄山归来不看岳", "recommend": "皖美山水风情" },
      { "name": "宏村", "type": "scenic", "lnglat": [117.9895, 29.9933], "desc": "画中的村庄，徽派建筑代表", "recommend": "皖美山水风情" },
      { "name": "九华山", "type": "scenic", "lnglat": [117.8083, 30.4866], "desc": "地藏菩萨道场，香火鼎盛", "recommend": "皖美山水风情" },
      { "name": "西递", "type": "scenic", "lnglat": [117.9883, 29.9100], "desc": "桃花源里人家，静谧的徽州古村", "recommend": "皖美山水风情" }
    ]
  },
  "福建省": {
    "summary": { "tags": ["武夷山", "鼓浪屿", "土楼"], "score": "9.1", "hotCount": 4, "poyPreviews": ["武夷山", "厦门鼓浪屿", "永定土楼", "福州三坊七巷"] },
    "hotspots": [
      { "name": "武夷山", "type": "scenic", "lnglat": [117.9853, 27.7160], "desc": "碧水丹山，大红袍的故乡", "recommend": "福气之旅探索" },
      { "name": "厦门鼓浪屿", "type": "scenic", "lnglat": [118.0662, 24.4463], "desc": "海上花园，文艺青年的圣地", "recommend": "福气之旅探索" },
      { "name": "永定土楼", "type": "scenic", "lnglat": [116.9703, 24.6332], "desc": "东方古城堡，客家人的智慧", "recommend": "福气之旅探索" },
      { "name": "福州三坊七巷", "type": "scenic", "lnglat": [119.2965, 26.0742], "desc": "明清建筑博物馆，半部中国近现代史", "recommend": "福气之旅探索" }
    ]
  },
  "江西省": {
    "summary": { "tags": ["庐山", "景德镇", "井冈山"], "score": "8.9", "hotCount": 3, "poyPreviews": ["庐山", "三清山", "景德镇古窑", "婺源篁岭"] },
    "hotspots": [
      { "name": "庐山", "type": "scenic", "lnglat": [115.9946, 29.5556], "desc": "不识庐山真面目，只缘身在此山中", "recommend": "红色与自然交融" },
      { "name": "三清山", "type": "scenic", "lnglat": [118.0646, 28.9070], "desc": "江南第一仙峰，道教名山", "recommend": "红色与自然交融" },
      { "name": "景德镇古窑民俗博览区", "type": "scenic", "lnglat": [117.2296, 29.2937], "desc": "千年瓷都的制瓷技艺活化石", "recommend": "红色与自然交融" },
      { "name": "婺源篁岭", "type": "scenic", "lnglat": [117.8617, 29.2616], "desc": "崖上古村，独特的晒秋农俗", "recommend": "红色与自然交融" }
    ]
  },
  "山东省": {
    "summary": { "tags": ["泰山", "趵突泉", "青岛啤酒"], "score": "9.2", "hotCount": 5, "poyPreviews": ["泰山", "曲阜三孔", "青岛崂山", "趵突泉"] },
    "hotspots": [
      { "name": "泰山", "type": "scenic", "lnglat": [117.1080, 36.2574], "desc": "五岳独尊，天下第一山", "recommend": "齐鲁大地揽胜" },
      { "name": "曲阜三孔", "type": "scenic", "lnglat": [116.9924, 35.5961], "desc": "孔府、孔庙、孔林，儒家文化发源地", "recommend": "齐鲁大地揽胜" },
      { "name": "青岛崂山", "type": "scenic", "lnglat": [120.6561, 36.1436], "desc": "海上第一名山，道教圣地", "recommend": "齐鲁大地揽胜" },
      { "name": "趵突泉", "type": "scenic", "lnglat": [117.0164, 36.6669], "desc": "天下第一泉，济南的灵魂", "recommend": "齐鲁大地揽胜" }
    ]
  },
  "河南省": {
    "summary": { "tags": ["龙门石窟", "少林寺", "古都"], "score": "9.1", "hotCount": 5, "poyPreviews": ["少林寺", "龙门石窟", "云台山", "清明上河园"] },
    "hotspots": [
      { "name": "少林寺", "type": "scenic", "lnglat": [112.9347, 34.5074], "desc": "天下武功出少林，禅宗祖庭", "recommend": "中原文化核心" },
      { "name": "龙门石窟", "type": "scenic", "lnglat": [112.4722, 34.5554], "desc": "中国石刻艺术宝库，盛唐风采", "recommend": "中原文化核心" },
      { "name": "云台山", "type": "scenic", "lnglat": [113.4031, 35.4414], "desc": "红岩绝壁，峡谷极品", "recommend": "中原文化核心" },
      { "name": "清明上河园", "type": "scenic", "lnglat": [114.3484, 34.7977], "desc": "一朝步入画卷，一日梦回千年", "recommend": "中原文化核心" }
    ]
  },
  "湖北省": {
    "summary": { "tags": ["武当山", "三峡", "黄鹤楼"], "score": "9.3", "hotCount": 5, "poyPreviews": ["黄鹤楼", "武当山", "长江三峡", "神农架"] },
    "hotspots": [
      { "name": "黄鹤楼", "type": "scenic", "lnglat": [114.3046, 30.5470], "desc": "天下江山第一楼，登高望远", "recommend": "荆楚风情体验" },
      { "name": "武当山", "type": "scenic", "lnglat": [111.0047, 32.4000], "desc": "道教圣地，太极拳发源地", "recommend": "荆楚风情体验" },
      { "name": "长江三峡", "type": "scenic", "lnglat": [110.9787, 30.9287], "desc": "高峡出平湖，壮丽的长江画廊", "recommend": "荆楚风情体验" },
      { "name": "神农架", "type": "scenic", "lnglat": [110.3668, 31.4682], "desc": "神秘的原始森林，华中屋脊", "recommend": "荆楚风情体验" }
    ]
  },
  "湖南省": {
    "summary": { "tags": ["张家界", "凤凰古城", "湘菜"], "score": "9.4", "hotCount": 5, "poyPreviews": ["张家界国家森林公园", "凤凰古城", "岳麓山", "南岳衡山"] },
    "hotspots": [
      { "name": "张家界国家森林公园", "type": "scenic", "lnglat": [110.4349, 29.2960], "desc": "潘多拉太远，张家界很近，奇峰三千", "recommend": "锦绣潇湘打卡" },
      { "name": "凤凰古城", "type": "scenic", "lnglat": [109.5942, 27.9549], "desc": "沈从文笔下的边城，沱江夜色迷人", "recommend": "锦绣潇湘打卡" },
      { "name": "岳麓山", "type": "scenic", "lnglat": [112.9367, 28.1860], "desc": "停车坐爱枫林晚，霜叶红于二月花", "recommend": "锦绣潇湘打卡" },
      { "name": "南岳衡山", "type": "scenic", "lnglat": [112.7397, 27.2456], "desc": "五岳独秀，祈福圣地", "recommend": "锦绣潇湘打卡" }
    ]
  },
  "广东省": {
    "summary": { "tags": ["早茶", "粤菜", "长隆"], "score": "9.5", "hotCount": 6, "poyPreviews": ["广州塔", "深圳世界之窗", "丹霞山", "长隆野生动物世界"] },
    "hotspots": [
      { "name": "广州塔", "type": "scenic", "lnglat": [113.3307, 23.1084], "desc": "小蛮腰，俯瞰羊城繁华", "recommend": "岭南风韵必游" },
      { "name": "深圳世界之窗", "type": "scenic", "lnglat": [113.9740, 22.5370], "desc": "一天看遍世界微缩景观", "recommend": "岭南风韵必游" },
      { "name": "丹霞山", "type": "scenic", "lnglat": [113.7478, 25.0251], "desc": "中国红石公园，丹霞地貌命名地", "recommend": "岭南风韵必游" },
      { "name": "长隆野生动物世界", "type": "scenic", "lnglat": [113.3297, 22.9993], "desc": "国内首屈一指的动物主题乐园", "recommend": "岭南风韵必游" }
    ]
  },
  "广西壮族自治区": {
    "summary": { "tags": ["桂林山水", "米粉", "壮族"], "score": "9.2", "hotCount": 4, "poyPreviews": ["桂林漓江", "阳朔西街", "德天跨国瀑布", "北海银滩"] },
    "hotspots": [
      { "name": "桂林漓江", "type": "scenic", "lnglat": [110.5004, 24.7721], "desc": "桂林山水甲天下，百里画廊", "recommend": "山水画卷探索" },
      { "name": "阳朔西街", "type": "scenic", "lnglat": [110.4894, 24.7781], "desc": "中西文化交融的风情街", "recommend": "山水画卷探索" },
      { "name": "德天跨国瀑布", "type": "scenic", "lnglat": [106.7554, 22.8585], "desc": "亚洲第一大跨国瀑布，气势恢宏", "recommend": "山水画卷探索" },
      { "name": "北海银滩", "type": "scenic", "lnglat": [109.1408, 21.4469], "desc": "天下第一滩，沙细白软", "recommend": "山水画卷探索" }
    ]
  },
  "海南省": {
    "summary": { "tags": ["椰岛", "热带", "三亚"], "score": "9.4", "hotCount": 5, "poyPreviews": ["天涯海角", "亚龙湾", "蜈支洲岛", "南山文化旅游区"] },
    "hotspots": [
      { "name": "天涯海角", "type": "scenic", "lnglat": [109.3504, 18.2295], "desc": "海畔巨石，象征爱情忠贞的浪漫之地", "recommend": "热带风情度假" },
      { "name": "亚龙湾", "type": "scenic", "lnglat": [109.6418, 18.2181], "desc": "天下第一湾，水清沙幼的高端度假区", "recommend": "热带风情度假" },
      { "name": "蜈支洲岛", "type": "scenic", "lnglat": [109.7624, 18.3101], "desc": "中国的马尔代夫，潜水胜地", "recommend": "热带风情度假" },
      { "name": "南山文化旅游区", "type": "scenic", "lnglat": [109.2087, 18.3083], "desc": "108米海上观音，震撼心灵", "recommend": "热带风情度假" }
    ]
  },
  "四川省": {
    "summary": { "tags": ["川菜", "国宝", "麻辣"], "score": "9.5", "hotCount": 6, "poyPreviews": ["九寨沟", "峨眉山", "青城山", "成都大熊猫繁育研究基地"] },
    "hotspots": [
      { "name": "九寨沟", "type": "scenic", "lnglat": [103.9193, 33.2668], "desc": "童话世界，水景之王", "recommend": "巴蜀秘境寻踪" },
      { "name": "峨眉山", "type": "scenic", "lnglat": [103.4842, 29.5850], "desc": "秀甲天下，普贤菩萨道场", "recommend": "巴蜀秘境寻踪" },
      { "name": "青城山", "type": "scenic", "lnglat": [103.5832, 30.9005], "desc": "青城天下幽，道教发源地之一", "recommend": "巴蜀秘境寻踪" },
      { "name": "大熊猫基地", "type": "scenic", "lnglat": [104.1019, 30.7352], "desc": "近距离接触国宝滚滚的绝佳地", "recommend": "巴蜀秘境寻踪" }
    ]
  },
  "贵州省": {
    "summary": { "tags": ["黄果树瀑布", "西江千户苗寨", "避暑"], "score": "9.0", "hotCount": 4, "poyPreviews": ["黄果树瀑布", "西江千户苗寨", "梵净山", "荔波小七孔"] },
    "hotspots": [
      { "name": "黄果树瀑布", "type": "scenic", "lnglat": [105.6732, 25.9891], "desc": "亚洲最大瀑布，飞流直下", "recommend": "多彩贵州体验" },
      { "name": "西江千户苗寨", "type": "scenic", "lnglat": [108.1971, 26.4928], "desc": "世界最大的苗族聚居村寨，万家灯火", "recommend": "多彩贵州体验" },
      { "name": "梵净山", "type": "scenic", "lnglat": [108.6891, 27.9205], "desc": "天空之城，弥勒菩萨道场", "recommend": "多彩贵州体验" },
      { "name": "荔波小七孔", "type": "scenic", "lnglat": [107.8846, 25.2715], "desc": "地球腰带上的绿宝石，水色如碧", "recommend": "多彩贵州体验" }
    ]
  },
  "云南省": {
    "summary": { "tags": ["七彩云南", "过桥米线", "民族"], "score": "9.6", "hotCount": 6, "poyPreviews": ["丽江古城", "大理洱海", "香格里拉", "石林"] },
    "hotspots": [
      { "name": "丽江古城", "type": "scenic", "lnglat": [100.2372, 26.8671], "desc": "雪山下的浪漫古城，慵懒慢生活", "recommend": "七彩风情漫游" },
      { "name": "大理洱海", "type": "scenic", "lnglat": [100.1971, 25.6029], "desc": "风花雪月，环湖骑行的文艺之地", "recommend": "七彩风情漫游" },
      { "name": "香格里拉", "type": "scenic", "lnglat": [99.7060, 27.8303], "desc": "心中的日月，离天堂最近的地方", "recommend": "七彩风情漫游" },
      { "name": "石林", "type": "scenic", "lnglat": [103.3229, 24.8185], "desc": "天下第一奇观，典型的喀斯特地貌", "recommend": "七彩风情漫游" }
    ]
  },
  "西藏自治区": {
    "summary": { "tags": ["布达拉宫", "净土", "藏传佛教"], "score": "9.8", "hotCount": 6, "poyPreviews": ["布达拉宫", "大昭寺", "纳木错", "雅鲁藏布大峡谷"] },
    "hotspots": [
      { "name": "布达拉宫", "type": "scenic", "lnglat": [91.1185, 29.6545], "desc": "世界屋脊的明珠，藏传佛教圣地", "recommend": "雪域高原朝圣" },
      { "name": "大昭寺", "type": "scenic", "lnglat": [91.1396, 29.6560], "desc": "拉萨信仰的中心，八廓街转经", "recommend": "雪域高原朝圣" },
      { "name": "纳木错", "type": "scenic", "lnglat": [90.8356, 30.7442], "desc": "西藏三大圣湖之一，纯净唯美", "recommend": "雪域高原朝圣" },
      { "name": "雅鲁藏布大峡谷", "type": "scenic", "lnglat": [94.9444, 29.6190], "desc": "地球上最深的峡谷，壮美震撼", "recommend": "雪域高原朝圣" }
    ]
  },
  "陕西省": {
    "summary": { "tags": ["兵马俑", "十三朝古都", "秦岭"], "score": "9.5", "hotCount": 6, "poyPreviews": ["秦始皇兵马俑", "华山", "大雁塔", "华清宫"] },
    "hotspots": [
      { "name": "秦始皇兵马俑", "type": "scenic", "lnglat": [109.2812, 34.3852], "desc": "世界第八大奇迹，地下军团", "recommend": "古都文化穿越" },
      { "name": "华山", "type": "scenic", "lnglat": [110.0898, 34.4793], "desc": "奇险天下第一山，长空栈道挑战", "recommend": "古都文化穿越" },
      { "name": "大雁塔", "type": "scenic", "lnglat": [108.9587, 34.2148], "desc": "玄奘译经之地，大唐长安地标", "recommend": "古都文化穿越" },
      { "name": "华清宫", "type": "scenic", "lnglat": [109.2160, 34.3630], "desc": "唐玄宗与杨贵妃的爱情见证地", "recommend": "古都文化穿越" }
    ]
  },
  "甘肃省": {
    "summary": { "tags": ["敦煌莫高窟", "张掖丹霞", "丝路"], "score": "9.2", "hotCount": 4, "poyPreviews": ["敦煌莫高窟", "鸣沙山月牙泉", "张掖丹霞", "嘉峪关"] },
    "hotspots": [
      { "name": "敦煌莫高窟", "type": "scenic", "lnglat": [94.8089, 40.0431], "desc": "东方卢浮宫，丝路壁画宝库", "recommend": "大漠戈壁寻踪" },
      { "name": "鸣沙山月牙泉", "type": "scenic", "lnglat": [94.6708, 40.0888], "desc": "山泉共处，沙水共生的沙漠奇观", "recommend": "大漠戈壁寻踪" },
      { "name": "张掖丹霞国家地质公园", "type": "scenic", "lnglat": [100.1141, 38.9608], "desc": "上帝打翻的调色盘，色彩斑斓", "recommend": "大漠戈壁寻踪" },
      { "name": "嘉峪关关城", "type": "scenic", "lnglat": [98.2165, 39.8044], "desc": "天下第一雄关，长城西端起点", "recommend": "大漠戈壁寻踪" }
    ]
  },
  "青海省": {
    "summary": { "tags": ["青海湖", "塔尔寺", "三江源"], "score": "9.1", "hotCount": 4, "poyPreviews": ["青海湖", "塔尔寺", "茶卡盐湖", "可可西里"] },
    "hotspots": [
      { "name": "青海湖", "type": "scenic", "lnglat": [100.1852, 36.9529], "desc": "中国最大的内陆咸水湖，夏日油菜花海", "recommend": "大西北环线必去" },
      { "name": "塔尔寺", "type": "scenic", "lnglat": [101.5735, 36.5905], "desc": "格鲁派圣地，艺术三绝闻名", "recommend": "大西北环线必去" },
      { "name": "茶卡盐湖", "type": "scenic", "lnglat": [99.0961, 36.7955], "desc": "中国的天空之境，浪漫倒影", "recommend": "大西北环线必去" },
      { "name": "可可西里", "type": "scenic", "lnglat": [92.3456, 35.1234], "desc": "神秘的无人区，藏羚羊的故乡", "recommend": "大西北环线必去" }
    ]
  },
  "宁夏回族自治区": {
    "summary": { "tags": ["沙坡头", "西夏王陵", "枸杞"], "score": "8.4", "hotCount": 3, "poyPreviews": ["沙坡头", "镇北堡西部影城", "沙湖", "西夏王陵"] },
    "hotspots": [
      { "name": "沙坡头", "type": "scenic", "lnglat": [105.1714, 37.4987], "desc": "大漠孤烟直，长河落日圆", "recommend": "塞上江南风情" },
      { "name": "镇北堡西部影城", "type": "scenic", "lnglat": [105.9815, 38.6333], "desc": "大话西游取景地，中国电影从这里走向世界", "recommend": "塞上江南风情" },
      { "name": "沙湖", "type": "scenic", "lnglat": [106.3559, 38.8144], "desc": "沙与水的交响乐，塞上明珠", "recommend": "塞上江南风情" },
      { "name": "西夏王陵", "type": "scenic", "lnglat": [105.9818, 38.4868], "desc": "东方金字塔，神秘的西夏古国遗迹", "recommend": "塞上江南风情" }
    ]
  },
  "新疆维吾尔自治区": {
    "summary": { "tags": ["天山天池", "喀纳斯", "羊肉串"], "score": "9.7", "hotCount": 6, "poyPreviews": ["天山天池", "喀纳斯湖", "那拉提草原", "葡萄沟"] },
    "hotspots": [
      { "name": "天山天池", "type": "scenic", "lnglat": [88.1313, 43.8888], "desc": "瑶池仙境，雪山掩映下的高山湖泊", "recommend": "大美新疆自驾" },
      { "name": "喀纳斯湖", "type": "scenic", "lnglat": [87.0395, 48.8190], "desc": "神仙的后花园，秋色无双", "recommend": "大美新疆自驾" },
      { "name": "那拉提草原", "type": "scenic", "lnglat": [84.0033, 43.2845], "desc": "空中草原，雪山交织的绿意", "recommend": "大美新疆自驾" },
      { "name": "葡萄沟", "type": "scenic", "lnglat": [89.2420, 42.9376], "desc": "吐鲁番的绿洲，品尝甜蜜瓜果", "recommend": "大美新疆自驾" }
    ]
  },
  "香港特别行政区": {
    "summary": { "tags": ["维多利亚港", "购物天堂", "迪士尼"], "score": "9.0", "hotCount": 4, "poyPreviews": ["香港迪士尼乐园", "维多利亚港", "太平山顶", "天坛大佛"] },
    "hotspots": [
      { "name": "香港迪士尼乐园", "type": "scenic", "lnglat": [114.0430, 22.3129], "desc": "充满奇妙色彩的主题乐园", "recommend": "东方之珠漫步" },
      { "name": "维多利亚港", "type": "scenic", "lnglat": [114.1701, 22.2871], "desc": "世界三大天然良港之一，绝美夜景", "recommend": "东方之珠漫步" },
      { "name": "太平山顶", "type": "scenic", "lnglat": [114.1514, 22.2712], "desc": "俯瞰繁华港岛的最高点", "recommend": "东方之珠漫步" },
      { "name": "天坛大佛", "type": "scenic", "lnglat": [113.9123, 22.2542], "desc": "全球最大的户外青铜座佛", "recommend": "东方之珠漫步" }
    ]
  },
  "澳门特别行政区": {
    "summary": { "tags": ["大三巴牌坊", "赌城", "葡式蛋挞"], "score": "8.6", "hotCount": 3, "poyPreviews": ["大三巴牌坊", "澳门旅游塔", "威尼斯人", "议事亭前地"] },
    "hotspots": [
      { "name": "大三巴牌坊", "type": "scenic", "lnglat": [113.5430, 22.1910], "desc": "澳门标志性建筑，圣保禄学院遗址", "recommend": "多元文化交汇" },
      { "name": "澳门旅游塔", "type": "scenic", "lnglat": [113.5424, 22.1772], "desc": "挑战高空蹦极，俯瞰全澳美景", "recommend": "多元文化交汇" },
      { "name": "威尼斯人度假村", "type": "scenic", "lnglat": [113.5720, 22.1517], "desc": "集娱乐、购物、酒店于一体的巨型度假城", "recommend": "多元文化交汇" },
      { "name": "议事亭前地", "type": "scenic", "lnglat": [113.5409, 22.1938], "desc": "充满葡国风情的波浪形碎石广场", "recommend": "多元文化交汇" }
    ]
  },
  "台湾省": {
    "summary": { "tags": ["台北101", "日月潭", "夜市"], "score": "9.2", "hotCount": 5, "poyPreviews": ["台北101", "日月潭", "阿里山", "野柳地质公园"] },
    "hotspots": [
      { "name": "台北101", "type": "scenic", "lnglat": [121.5646, 25.0330], "desc": "曾经的世界第一高楼，台北地标", "recommend": "宝岛风光无限" },
      { "name": "日月潭", "type": "scenic", "lnglat": [120.9506, 23.8522], "desc": "群山环抱中的美丽高山湖泊", "recommend": "宝岛风光无限" },
      { "name": "阿里山", "type": "scenic", "lnglat": [120.8012, 23.5161], "desc": "日出、云海、晚霞、森林与高山铁路", "recommend": "宝岛风光无限" },
      { "name": "野柳地质公园", "type": "scenic", "lnglat": [121.6882, 25.2075], "desc": "罕见的海蚀奇观，著名的女王头", "recommend": "宝岛风光无限" }
    ]
  }
};