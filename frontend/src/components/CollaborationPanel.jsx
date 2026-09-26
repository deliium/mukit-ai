import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { roleAllows } from '../utils/collaborationAccess.js';

const Stack = styled.div`
  display: grid;
  gap: 12px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Field = styled.input`
  min-height: 36px;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
`;

const Button = styled.button`
  min-height: 36px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid #4f46e5;
  background: #eef2ff;
  font-weight: 600;
  cursor: pointer;
`;

const List = styled.ul`
  margin: 0;
  padding-left: 18px;
  color: #334155;
`;

const CollaborationPanel = () => {
  const projectId = useMusicStore((state) => state.currentProjectId);
  const role = useMusicStore((state) => state.projectCollaboration?.role || null);
  const actors = useMusicStore((state) => state.collaborationActors);
  const actorId = useMusicStore((state) => state.collaborationActorId);
  const members = useMusicStore((state) => state.collaborationMembers);
  const comments = useMusicStore((state) => state.collaborationComments);
  const reviews = useMusicStore((state) => state.collaborationReviews);
  const activity = useMusicStore((state) => state.collaborationActivity);
  const selectCollaborationActor = useMusicStore((state) => state.selectCollaborationActor);
  const createCollaborationActorByName = useMusicStore((state) => state.createCollaborationActorByName);
  const shareProjectMember = useMusicStore((state) => state.shareProjectMember);
  const postCollaborationComment = useMusicStore((state) => state.postCollaborationComment);
  const openCollaborationReview = useMusicStore((state) => state.openCollaborationReview);
  const decideCollaborationReview = useMusicStore((state) => state.decideCollaborationReview);
  const refreshCollaboration = useMusicStore((state) => state.refreshCollaboration);
  const [displayName, setDisplayName] = useState('');
  const [shareActorId, setShareActorId] = useState('');
  const [shareRole, setShareRole] = useState('editor');
  const [commentBody, setCommentBody] = useState('');
  const [sectionId, setSectionId] = useState('section-1');
  const [revisionId, setRevisionId] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    refreshCollaboration().catch((err) => setError(err.message));
  }, [projectId, refreshCollaboration]);

  const canShare = roleAllows(role, 'share');
  const canComment = roleAllows(role, 'comment');
  const canDecide = roleAllows(role, 'review_decide');
  const canOpen = roleAllows(role, 'review_open');

  return (
    <Stack data-testid="collaboration-panel">
      <Row>
        <label htmlFor="collaboration-actor">Actor</label>
        <select
          id="collaboration-actor"
          value={actorId}
          onChange={(event) => {
            selectCollaborationActor(event.target.value).catch((err) => setError(err.message));
          }}
        >
          <option value="">local (header omitted)</option>
          {actors.map((actor) => (
            <option key={actor.id} value={actor.id}>{actor.display_name}</option>
          ))}
        </select>
        <Field
          aria-label="New actor display name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <Button
          type="button"
          onClick={() => {
            createCollaborationActorByName(displayName)
              .then(() => setDisplayName(''))
              .catch((err) => setError(err.message));
          }}
        >
          Add actor
        </Button>
      </Row>
      {canShare ? (
        <Row>
          <Field
            aria-label="Share actor id"
            value={shareActorId}
            onChange={(event) => setShareActorId(event.target.value)}
          />
          <select aria-label="Share role" value={shareRole} onChange={(event) => setShareRole(event.target.value)}>
            <option value="editor">editor</option>
            <option value="commenter">commenter</option>
            <option value="viewer">viewer</option>
          </select>
          <Button
            type="button"
            onClick={() => {
              shareProjectMember(shareActorId, shareRole).catch((err) => setError(err.message));
            }}
          >
            Share
          </Button>
        </Row>
      ) : null}
      {canComment ? (
        <Row>
          <Field
            aria-label="Comment section id"
            value={sectionId}
            onChange={(event) => setSectionId(event.target.value)}
          />
          <Field
            aria-label="Comment revision id"
            value={revisionId}
            onChange={(event) => setRevisionId(event.target.value)}
          />
          <Field
            aria-label="Comment body"
            value={commentBody}
            onChange={(event) => setCommentBody(event.target.value)}
          />
          <Button
            type="button"
            onClick={() => {
              postCollaborationComment({
                targetKind: sectionId ? 'section' : 'project',
                body: commentBody,
                sectionId: sectionId || null,
                revisionId: revisionId || null,
              })
                .then(() => setCommentBody(''))
                .catch((err) => setError(err.message));
            }}
          >
            Comment
          </Button>
        </Row>
      ) : null}
      <List>
        {members.map((member) => (
          <li key={member.actor_id}>{member.actor_id} · {member.role}</li>
        ))}
      </List>
      <List>
        {comments.map((comment) => (
          <li key={comment.id}>{comment.target_kind}: {comment.body}</li>
        ))}
      </List>
      <List>
        {reviews.map((review) => (
          <li key={review.id}>
            {review.status} · {review.origin}
            {canOpen && review.status !== 'open' ? null : null}
            {canDecide && review.status === 'open' ? (
              <>
                <Button type="button" onClick={() => decideCollaborationReview(review.id, 'approve').catch((err) => setError(err.message))}>Approve</Button>
                <Button type="button" onClick={() => decideCollaborationReview(review.id, 'reject').catch((err) => setError(err.message))}>Reject</Button>
              </>
            ) : null}
          </li>
        ))}
      </List>
      {canOpen ? (
        <Row>
          <Field
            aria-label="Review revision id"
            value={revisionId}
            onChange={(event) => setRevisionId(event.target.value)}
          />
          <Button type="button" onClick={() => openCollaborationReview(revisionId).catch((err) => setError(err.message))}>
            Open review
          </Button>
        </Row>
      ) : null}
      <List>
        {activity.map((row) => (
          <li key={row.id}>{row.kind}</li>
        ))}
      </List>
      {error ? <p role="alert">{error}</p> : null}
    </Stack>
  );
};

export default CollaborationPanel;
