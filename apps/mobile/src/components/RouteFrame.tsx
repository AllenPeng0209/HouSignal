import type { ComponentType } from 'react';
import { useLocalSearchParams } from 'expo-router';
import { useApp } from '../state/AppContext';
import { Button, Empty, Page } from './ui';
import type { Params } from '../lib/contracts';

export function RouteFrame({ component: Component, projectRequired = true }: { component: ComponentType<any>; projectRequired?: boolean }) {
  const app = useApp();
  const values = useLocalSearchParams<{ id?: string; conversationId?: string }>();
  const params: Params = { id: typeof values.id === 'string' ? values.id : undefined, conversationId: typeof values.conversationId === 'string' ? values.conversationId : undefined };
  if (projectRequired && !app.project) return <Page><Empty title="先选择一个项目" description="资料、内容和客户记录都保存在对应项目中。" action={<Button title="选择或新建项目" onPress={() => app.navigate('projects')} />} /></Page>;
  return <Component key={`${app.session.user.id}:${app.session.organization.id}:${app.session.role}:${app.project?.id || 'none'}:${params.id || ''}`} params={params} />;
}
