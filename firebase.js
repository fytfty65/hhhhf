// src/firebase.js
import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";
import { getStorage } from "firebase/storage";

const firebaseConfig = {
  apiKey: ai_service.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: ai_service.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: ai_service.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: ai_service.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: ai_service.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: ai_service.env.NEXT_PUBLIC_FIREBASE_APP_ID
};

// 初始化 App 实例
const app = initializeApp(firebaseConfig);

// 按需导出所需服务（Tree-shaking 模块化设计）
export const auth = getAuth(app);
export const db = getFirestore(app);
export const storage = getStorage(app);
export default app;