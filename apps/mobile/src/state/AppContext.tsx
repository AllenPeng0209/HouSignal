import { createContext, useContext } from 'react';
import type { AppState } from '../lib/contracts';
export const AppContext = createContext<AppState | null>(null);
export function useApp(): AppState {
  const context = useContext(AppContext);
  if (!context) throw new Error('App context is unavailable');
  return context;
}
