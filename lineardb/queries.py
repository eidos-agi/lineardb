from __future__ import annotations

VIEWER = """
query Viewer {
  viewer {
    id
    name
    email
    organization {
      id
      name
      urlKey
    }
  }
}
""".strip()

TEAMS = """
query Teams($first: Int!, $after: String) {
  teams(first: $first, after: $after) {
    nodes {
      id
      key
      name
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()

TEAM_ISSUES = """
query TeamIssues($teamKey: String!, $first: Int!, $after: String) {
  teams(filter: { key: { eq: $teamKey } }, first: 1) {
    nodes {
      id
      key
      name
      issues(first: $first, after: $after) {
        nodes {
          id
          identifier
          title
          url
          priority
          priorityLabel
          createdAt
          updatedAt
          completedAt
          canceledAt
          dueDate
          state { id name type }
          assignee { id name }
          project { id name url }
          cycle { id name }
          labels { nodes { id name } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

ISSUE_COMMENTS = """
query IssueComments($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    comments(first: $first, after: $after, includeArchived: true) {
      nodes {
        id
        createdAt
        updatedAt
        archivedAt
        body
        bodyData
        url
        reactionData
        user { id name }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()

ISSUE_ATTACHMENTS = """
query IssueAttachments($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    attachments(first: $first, after: $after, includeArchived: true) {
      nodes {
        id
        createdAt
        updatedAt
        archivedAt
        title
        subtitle
        url
        metadata
        source
        sourceType
        bodyData
        creator { id name }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()

ISSUE_HISTORY = """
query IssueHistory($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    history(first: $first, after: $after, includeArchived: true) {
      nodes {
        id
        createdAt
        updatedAt
        archivedAt
        actorId
        updatedDescription
        fromAssigneeId
        toAssigneeId
        fromProjectId
        toProjectId
        fromPriority
        toPriority
        fromDueDate
        toDueDate
        actor { id name }
        fromState { id name type }
        toState { id name type }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()

ISSUE_STATE_HISTORY = """
query IssueStateHistory($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    stateHistory(first: $first, after: $after) {
      nodes {
        id
        stateId
        startedAt
        endedAt
        state { id name type }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()
