"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditFilters, MemberChange, RecordingFilters, Role, SegmentEdit, WorkspaceSettings } from "@/lib/api/types";

export const queryKeys = {
  me: ["me"] as const,
  devices: ["devices"] as const,
  recordings: (filters: RecordingFilters) => ["recordings", filters] as const,
  recording: (id: string) => ["recording", id] as const,
  people: ["people"] as const,
  members: ["members"] as const,
  invitations: ["invitations"] as const,
  summary: ["summary"] as const,
  settings: ["settings"] as const,
  processing: ["processing"] as const,
  audit: (filters: AuditFilters) => ["audit", filters] as const,
};

export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: api.auth.me, retry: false, staleTime: 60_000 });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { email: string; password: string }) => api.auth.login(input.email, input.password),
    onSuccess: (user) => client.setQueryData(queryKeys.me, user),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.auth.logout,
    // Drop every cached query so the next user never sees the previous user's data.
    onSettled: () => client.clear(),
  });
}

export function useDevices() {
  return useQuery({ queryKey: queryKeys.devices, queryFn: api.devices.list });
}

export function useConnectDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.devices.connect(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.devices }),
  });
}

export function useDisconnectDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.devices.disconnect(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.devices }),
  });
}

export function useCreatePairing() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.devices.createPairing(name),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.devices }),
  });
}

export function useRecordings(filters: RecordingFilters) {
  return useQuery({
    queryKey: queryKeys.recordings(filters),
    queryFn: () => api.recordings.list(filters),
    placeholderData: keepPreviousData,
  });
}

export function useRecording(id: string) {
  return useQuery({ queryKey: queryKeys.recording(id), queryFn: () => api.recordings.get(id) });
}

export function useDeleteRecording() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.recordings.remove(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["recordings"] });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

export function useUpdateTranscript(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (edits: SegmentEdit[]) => api.recordings.updateTranscript(id, edits),
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.recording(id), detail);
      client.invalidateQueries({ queryKey: ["recordings"] });
    },
  });
}

export function usePeople() {
  return useQuery({ queryKey: queryKeys.people, queryFn: api.people.list });
}

export function useCreatePerson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (displayName: string) => api.people.create(displayName),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  });
}

/** A worker starting or stopping is not announced, so this also checks on a timer. */
export function useProcessing() {
  return useQuery({
    queryKey: queryKeys.processing,
    queryFn: api.processing.status,
    refetchInterval: 15_000,
    // Not worth an error screen: the notices that use it simply do not appear if it fails.
    retry: false,
  });
}

export function useAuditEvents(filters: AuditFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: () => api.audit.list(filters),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useReprocess() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.recordings.reprocess(id),
    onSuccess: (_recording, id) => {
      client.invalidateQueries({ queryKey: queryKeys.recording(id) });
      client.invalidateQueries({ queryKey: ["recordings"] });
      client.invalidateQueries({ queryKey: queryKeys.summary });
      client.invalidateQueries({ queryKey: queryKeys.processing });
    },
  });
}

export function useEnrollSample() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; wav: Blob }) => api.people.enrollSample(input.id, input.wav),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  });
}

export function useAddSignatureSample() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; wav: Blob }) => api.people.addSignatureSample(input.id, input.wav),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  });
}

/** A lookup, not a change: nothing is cached or refreshed. */
export function useMatchSignature() {
  return useMutation({ mutationFn: (wav: Blob) => api.people.matchSignature(wav) });
}

export function useRemoveVoice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.people.removeVoice(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.people });
      client.invalidateQueries({ queryKey: ["recordings"] });
      client.invalidateQueries({ queryKey: ["recording"] });
    },
  });
}

export function useRecordConsent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; granted: boolean }) => api.people.recordConsent(input.id, input.granted),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.people }),
  });
}

export function useDeletePerson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.people.remove(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.people });
      // Transcript lines that named this person now read "Unknown speaker".
      client.invalidateQueries({ queryKey: ["recordings"] });
      client.invalidateQueries({ queryKey: ["recording"] });
    },
  });
}

export function useAssignDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; userId: string | null }) => api.devices.assign(input.id, input.userId),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.devices }),
  });
}

export function useRevokeDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.devices.revoke(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.devices });
      client.invalidateQueries({ queryKey: queryKeys.summary });
    },
  });
}

/** `enabled` keeps people who cannot manage the team from asking for data they may not see. */
export function useMembers(enabled = true) {
  return useQuery({ queryKey: queryKeys.members, queryFn: api.team.members, enabled });
}

export function useInvitations(enabled = true) {
  return useQuery({ queryKey: queryKeys.invitations, queryFn: api.team.invitations, enabled });
}

export function useInvite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { email: string; role: Role }) => api.team.invite(input),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}

export function useRevokeInvitation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.team.revokeInvitation(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}

export function useUpdateMember() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; change: MemberChange }) => api.team.updateMember(input.id, input.change),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.members }),
  });
}

export function useEndMemberSessions() {
  return useMutation({ mutationFn: (id: string) => api.team.endMemberSessions(id) });
}

export function useSummary() {
  return useQuery({ queryKey: queryKeys.summary, queryFn: api.summary });
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.settings.get });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<WorkspaceSettings>) => api.settings.update(input),
    onSuccess: (settings) => client.setQueryData(queryKeys.settings, settings),
  });
}
