import React from "react";
import { StyleProp, TextStyle, View, ViewStyle } from "react-native";
import ActivityIcon from "lucide-react-native/icons/activity";
import ArrowRight from "lucide-react-native/icons/arrow-right";
import Bot from "lucide-react-native/icons/bot";
import Camera from "lucide-react-native/icons/camera";
import Check from "lucide-react-native/icons/check";
import CheckCircle2 from "lucide-react-native/icons/circle-check";
import ChevronDown from "lucide-react-native/icons/chevron-down";
import ChevronUp from "lucide-react-native/icons/chevron-up";
import CircleAlert from "lucide-react-native/icons/circle-alert";
import CircleCheck from "lucide-react-native/icons/circle-check";
import CircleUserRound from "lucide-react-native/icons/circle-user-round";
import Clock from "lucide-react-native/icons/clock";
import Cpu from "lucide-react-native/icons/cpu";
import Database from "lucide-react-native/icons/database";
import Download from "lucide-react-native/icons/download";
import Ellipsis from "lucide-react-native/icons/ellipsis";
import FileSearch from "lucide-react-native/icons/file-search";
import FileText from "lucide-react-native/icons/file-text";
import FlaskConical from "lucide-react-native/icons/flask-conical";
import Gauge from "lucide-react-native/icons/gauge";
import Handshake from "lucide-react-native/icons/handshake";
import HardDrive from "lucide-react-native/icons/hard-drive";
import HeartHandshake from "lucide-react-native/icons/heart-handshake";
import KeyRound from "lucide-react-native/icons/key-round";
import ListChecks from "lucide-react-native/icons/list-checks";
import Loader from "lucide-react-native/icons/loader";
import Lock from "lucide-react-native/icons/lock";
import MessageSquare from "lucide-react-native/icons/message-square";
import MessagesSquare from "lucide-react-native/icons/messages-square";
import Mic from "lucide-react-native/icons/mic";
import MicOff from "lucide-react-native/icons/mic-off";
import PackageCheck from "lucide-react-native/icons/package-check";
import Play from "lucide-react-native/icons/play";
import Pause from "lucide-react-native/icons/pause";
import RefreshCw from "lucide-react-native/icons/refresh-cw";
import ScanSearch from "lucide-react-native/icons/scan-search";
import Search from "lucide-react-native/icons/search";
import Share2 from "lucide-react-native/icons/share-2";
import ShieldCheck from "lucide-react-native/icons/shield-check";
import ShieldLock from "lucide-react-native/icons/shield-lock";
import Settings2 from "lucide-react-native/icons/settings-2";
import Smartphone from "lucide-react-native/icons/smartphone";
import Sparkles from "lucide-react-native/icons/sparkles";
import Trash2 from "lucide-react-native/icons/trash";
import UserRound from "lucide-react-native/icons/user-round";
import Volume2 from "lucide-react-native/icons/volume-2";
import Waves from "lucide-react-native/icons/waves-horizontal";
import Zap from "lucide-react-native/icons/zap";
import { icon, colors, themes } from "@/theme/tokens";
import { ThemeContext } from "@/lib/themeContext";

export type IconName =
  | "activity"
  | "arrowRight"
  | "bot"
  | "camera"
  | "check"
  | "checkCircle"
  | "chevronDown"
  | "chevronUp"
  | "circleAlert"
  | "circleCheck"
  | "circleUser"
  | "clock"
  | "cpu"
  | "database"
  | "download"
  | "ellipsis"
  | "fileSearch"
  | "fileText"
  | "flask"
  | "gauge"
  | "handshake"
  | "hardDrive"
  | "heartHandshake"
  | "keyRound"
  | "listChecks"
  | "loader"
  | "lock"
  | "messageSquare"
  | "messagesSquare"
  | "mic"
  | "micOff"
  | "packageCheck"
  | "pause"
  | "play"
  | "refresh"
  | "scanSearch"
  | "search"
  | "share"
  | "shieldCheck"
  | "shieldLock"
  | "settings"
  | "smartphone"
  | "sparkles"
  | "trash"
  | "userRound"
  | "volume"
  | "waves"
  | "zap";

export const iconMap: Record<IconName, React.ComponentType<{ size?: number; color?: string; strokeWidth?: number }>> = {
  activity: ActivityIcon,
  arrowRight: ArrowRight,
  bot: Bot,
  camera: Camera,
  check: Check,
  checkCircle: CheckCircle2,
  chevronDown: ChevronDown,
  chevronUp: ChevronUp,
  circleAlert: CircleAlert,
  circleCheck: CircleCheck,
  circleUser: CircleUserRound,
  clock: Clock,
  cpu: Cpu,
  database: Database,
  download: Download,
  ellipsis: Ellipsis,
  fileSearch: FileSearch,
  fileText: FileText,
  flask: FlaskConical,
  gauge: Gauge,
  handshake: Handshake,
  hardDrive: HardDrive,
  heartHandshake: HeartHandshake,
  keyRound: KeyRound,
  listChecks: ListChecks,
  loader: Loader,
  lock: Lock,
  messageSquare: MessageSquare,
  messagesSquare: MessagesSquare,
  mic: Mic,
  micOff: MicOff,
  packageCheck: PackageCheck,
  pause: Pause,
  play: Play,
  refresh: RefreshCw,
  scanSearch: ScanSearch,
  search: Search,
  share: Share2,
  shieldCheck: ShieldCheck,
  shieldLock: ShieldLock,
  settings: Settings2,
  smartphone: Smartphone,
  sparkles: Sparkles,
  trash: Trash2,
  userRound: UserRound,
  volume: Volume2,
  waves: Waves,
  zap: Zap,
};

export interface AppIconProps {
  name: IconName;
  size?: keyof typeof icon.sizes | number;
  color?: string;
  strokeWidth?: number;
  style?: StyleProp<ViewStyle>;
  accessible?: boolean;
  accessibilityLabel?: string;
}

export function AppIcon({ name, size = "inline", color, strokeWidth = icon.strokeWidth, style, accessible = true, accessibilityLabel }: AppIconProps) {
  const px = typeof size === "number" ? size : icon.sizes[size];
  const Glyph = iconMap[name];
  const label = accessibilityLabel ?? (name === "loader" ? "Loading" : undefined);
  const ink = color ?? colors.textMuted;
  return (
    <View style={style} accessible={accessible} accessibilityLabel={label} accessibilityRole={accessible ? "image" : undefined}>
      <Glyph size={px} color={ink} strokeWidth={strokeWidth} />
    </View>
  );
}

export function iconSize(name: keyof typeof icon.sizes): number {
  return icon.sizes[name];
}
