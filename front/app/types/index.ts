export type Phase = 'drafting' | 'deduction' | 'decision';

export interface UserProfile {
  id: string;
  username: string;
  nickname: string;
  avatarSeed: string;
  avatarUrl?: string;
  signature?: string;
  token?: string;
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
  photos?: string[];
  mapImage?: string;
  amapUrl?: string;
  name: string;
  type?: string;
  className?: string;
  index?: number;
  onPhotoClick?: () => void;
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
  onRetry?: () => void;
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
