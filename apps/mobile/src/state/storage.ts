import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { createStorage, type StoreAdapter } from './storage-core';

export type { StoredCredentials } from './storage-core';

const privateStore: StoreAdapter = Platform.OS === 'web' ? {
  get: async key => typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem(key),
  set: async (key, value) => {
    if (typeof sessionStorage === 'undefined') throw new Error('当前浏览器无法保存会话，请允许站点使用会话存储');
    sessionStorage.setItem(key, value);
  },
  remove: async key => { if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(key); },
} : {
  get: key => SecureStore.getItemAsync(key),
  set: (key, value) => SecureStore.setItemAsync(key, value, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY }),
  remove: key => SecureStore.deleteItemAsync(key),
};
const publicStore: StoreAdapter = {
  get: key => AsyncStorage.getItem(key), set: (key, value) => AsyncStorage.setItem(key, value),
  remove: key => AsyncStorage.removeItem(key),
};
const storage = createStorage(privateStore, publicStore, __DEV__);
export const { readCredentials, saveCredentials, clearCredentials, readPreference, savePreference } = storage;
