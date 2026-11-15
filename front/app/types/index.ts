export type Phase = 'drafting' | 'deduction' | 'decision';

export interface UserProfile {
  id: string;
  username: string;
  nickname: string;
  avatarSeed: string;
  avatarUrl?: string;
  signature?: string;
  token?: string;
  // 免注册试用创建的临时账号。它能正常建房、协作、保存行程，
  // 但登录态只存在于当前浏览器，且长期不用会被服务端清理。
  isGuest?: boolean;
  // 服务端 ADMIN_USER_IDS 白名单算出的管理员身份，只用来决定是否展示管理入口。
  // 权限判断始终在服务端，这里改 true 不会获得任何额外能力。
  isAdmin?: boolean;
  isLoggedIn: boolean;
}

export interface RoomMember {
  id: string;
  name: string;
  role: string;
  intent: string;
  avatarSeed: string;
  avatarUrl?: string;
}

export interface PoiImageProps {
  photos?: string[];          // 实景照片URL数组（自动轮询）
  mapImage?: string;          // 高德静态地图兜底
  amapUrl?: string;           // 高德地图跳转链接（用于兜底卡片）
  name: string;               // 地点名称
  type?: string;              // 地点类型（风景/餐饮/住宿等）
  className?: string;
  index?: number;             // 序号
  onPhotoClick?: () => void;  // 点击兜底卡片时触发（打开实景照片搜索）
}

export interface ProfileTrip {
  ID: string;
  UserID: string;
  Title: string;
  DestCity: string;
  Content: string;
  CreatedAt: string;
}

export interface CommunityPostItem {
  id: string;
  user_id: string;
  author: string;
  author_avatar: string;
  author_avatar_url: string;
  title: string;
  content: string;
  dest_city: string;
  tags?: string[];
  likes: number;
  comments: number;
  favorites?: number;
  favorited?: boolean;
  heat?: number;
  created_at: string;
}

export interface CommunityComment {
  id: string;
  post_id: string;
  user_id: string;
  author: string;
  author_avatar: string;
  author_avatar_url: string;
  content: string;
  parent_id: string;
  created_at: string;
}

export interface DeductionPanelProps {
  latestLog?: string;
  error?: string | null;
  timedOut?: boolean;
  // 真实的等待时长（秒），只在规划进行中上报；失败/超时态不显示。
  elapsedSeconds?: number;
  onRetry?: () => void;
  onCancel?: () => void;
  onBack?: () => void;
  reasoningSteps?: ReasoningStep[];
}

export interface ReasoningStep {
  agent: string;
  action: 'claim' | 'compromise' | 'decision';
  claim: string;
  basis: string;
  ts?: string;
}

export type ActivityEvt = { id: string; ts: number; type: 'vote' | 'join' | 'consensus' | 'typing' | 'split'; text: string };
