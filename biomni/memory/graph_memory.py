import os
from neo4j import GraphDatabase

class GraphMemory:
    def __init__(self):
        # 도커 환경변수 우선 적용
        self.uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "biomnipassword")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self):
        self.driver.close()

    def save_error_and_reflection(self, task_desc, tool_name, error_msg, reflection):
        query = """
        MERGE (t:Task {name: $task})
        MERGE (tl:Tool {name: $tool})
        MERGE (e:Error {message: $error})
        MERGE (i:Insight {content: $reflection, processed: false})
        MERGE (t)-[:USED]->(tl) MERGE (tl)-[:RAISED]->(e) MERGE (e)-[:RESOLVED_BY]->(i)
        """
        with self.driver.session() as session:
            session.run(query, task=task_desc, tool=tool_name, error=error_msg, reflection=reflection)

    def fetch_global_insights(self, tool_name):
        query = "MATCH (tl:Tool {name: $tool})-[:HAS_GLOBAL_INSIGHT]->(gi:GlobalInsight) RETURN gi.content AS content"
        with self.driver.session() as session:
            return [{"content": r["content"]} for r in session.run(query, tool=tool_name)]
        
    def update_insight_feedback(self, task_name, tool_name, is_correct, agent_output):
        vote_change = 1 if is_correct else -1
        query = """
        // 해당 도구의 인사이트를 찾고 점수를 업데이트 (강화학습적 요소)
        MATCH (tl:Tool {name: $tool})-[:HAS_GLOBAL_INSIGHT]->(gi:GlobalInsight)
        SET gi.upvotes = coalesce(gi.upvotes, 0) + $vote_change
        WITH tl
        
        // 오답일 경우, 추후 분석을 위해 실패 궤적 저장 (Reflexion 트리거용)
        FOREACH(ignoreMe IN CASE WHEN $is_correct = false THEN [1] ELSE [] END |
            MERGE (t:Task {name: $task})
            MERGE (f:FailedTrajectory {output: $output})
            MERGE (t)-[:FAILED_WITH]->(f)-[:USED_TOOL]->(tl)
        )
        """
        with self.driver.session() as session:
            session.run(query, task=task_name, tool=tool_name, is_correct=is_correct, 
                        vote_change=vote_change, output=agent_output)