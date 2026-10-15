export function permissionsFor(session: {role: string; project_ids?: string[] | null}) {
  const {role} = session;
  return {canManage:['owner','admin'].includes(role) && session.project_ids === null,
    canEdit:['owner','admin','content'].includes(role),
    canEditData:['owner','admin','editor','content','reviewer'].includes(role),
    canReview:['owner','admin','reviewer'].includes(role),
    canSales:['owner','admin','sales'].includes(role)};
}
