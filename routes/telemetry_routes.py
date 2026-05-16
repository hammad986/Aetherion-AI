import os
import time
from flask import Blueprint, jsonify, request, Response

telemetry_bp = Blueprint('telemetry_routes', __name__)

# ── Metrics ───────────────────────────────────────────────────────────────────

@telemetry_bp.route('/metrics')
def _metrics_endpoint():
    remote = request.remote_addr or ''
    admin_key = request.headers.get('X-Admin-Key', '')
    configured_key = os.getenv('ADMIN_METRICS_KEY', '')
    is_local = remote in ('127.0.0.1', '::1', 'localhost')
    is_authorized = is_local or (configured_key and admin_key == configured_key)
    if not is_authorized:
        return Response('Forbidden', status=403, mimetype='text/plain')
    try:
        from infra.telemetry import get_telemetry
        body = get_telemetry().export_prometheus()
        return Response(body, status=200, mimetype='text/plain; version=0.0.4; charset=utf-8')
    except Exception as e:
        return Response(f'# ERROR: {e}', status=500, mimetype='text/plain')


# ── Infra ─────────────────────────────────────────────────────────────────────

@telemetry_bp.route('/api/infra/health')
def _infra_health():
    try:
        from infra.db_adapter import get_db
        from infra.event_bus import get_event_bus
        from infra.resilience import global_degraded_mode
        from infra.telemetry import get_telemetry
        tel = get_telemetry().snapshot()
        return jsonify({
            'ok': not global_degraded_mode.is_any_degraded(),
            'database': get_db('sessions.db').health_check(),
            'event_bus': get_event_bus().health(),
            'degraded': global_degraded_mode.snapshot(),
            'telemetry': tel['counters'],
            'ts': time.time(),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@telemetry_bp.route('/api/infra/resilience')
def _infra_resilience():
    try:
        from infra.resilience import global_recovery_playbook
        return jsonify(global_recovery_playbook.snapshot())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/infra/tenants')
def _infra_tenants():
    try:
        from infra.tenant import global_tenant_registry
        return jsonify({'tenants': global_tenant_registry.snapshot(),
                        'total_sessions': global_tenant_registry.session_count()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/infra/telemetry')
def _infra_telemetry():
    try:
        from infra.telemetry import get_telemetry
        return jsonify(get_telemetry().snapshot())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── DevOps ────────────────────────────────────────────────────────────────────

@telemetry_bp.route('/api/devops/health')
def _devops_health():
    try:
        from devops.health_monitor import get_health_monitor
        return jsonify(get_health_monitor().snapshot_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/playbook')
def _devops_playbook():
    try:
        from devops.playbook_engine import global_playbook_engine
        return jsonify(global_playbook_engine.snapshot())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/playbook/confirm', methods=['POST'])
def _devops_playbook_confirm():
    data = request.get_json(silent=True) or {}
    run_id   = data.get('run_id', '')
    operator = data.get('operator', 'unknown')
    try:
        from devops.playbook_engine import global_playbook_engine
        result = global_playbook_engine.confirm(run_id, operator)
        return jsonify({'ok': bool(result), 'status': result.status.value if result else None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/playbook/veto', methods=['POST'])
def _devops_playbook_veto():
    data = request.get_json(silent=True) or {}
    run_id   = data.get('run_id', '')
    operator = data.get('operator', 'unknown')
    try:
        from devops.playbook_engine import global_playbook_engine
        ok = global_playbook_engine.veto(run_id, operator)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/deployment')
def _devops_deployment():
    try:
        from devops.deployment_governor import global_deployment_governor
        return jsonify({
            'snapshot':          global_deployment_governor.snapshot(),
            'rollback_history':  global_deployment_governor.rollback_history(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/deployment/register', methods=['POST'])
def _devops_deploy_register():
    data = request.get_json(silent=True) or {}
    version  = data.get('version', 'unknown')
    operator = data.get('operator', 'unknown')
    try:
        from devops.deployment_governor import global_deployment_governor
        dep = global_deployment_governor.register_deploy(version, operator)
        return jsonify({'deployment_id': dep.deployment_id, 'status': dep.status.value})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/deployment/promote', methods=['POST'])
def _devops_deploy_promote():
    data = request.get_json(silent=True) or {}
    operator = data.get('operator', 'unknown')
    try:
        from devops.deployment_governor import global_deployment_governor
        ok = global_deployment_governor.promote_green(operator)
        return jsonify({'ok': ok, 'promoted': ok})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/deployment/rollback', methods=['POST'])
def _devops_deploy_rollback():
    data = request.get_json(silent=True) or {}
    reason   = data.get('reason', 'Operator-triggered rollback')
    operator = data.get('operator', 'unknown')
    try:
        from devops.deployment_governor import global_deployment_governor
        msg = global_deployment_governor.trigger_rollback(reason, operator)
        return jsonify({'ok': True, 'message': msg})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/disaster_recovery')
def _devops_dr():
    try:
        from devops.disaster_recovery import global_disaster_recovery
        return jsonify({
            'stats':          global_disaster_recovery.snapshot_stats(),
            'snapshots':      global_disaster_recovery.snapshot_list(10),
            'restore_jobs':   global_disaster_recovery.restore_job_list(5),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/disaster_recovery/snapshot', methods=['POST'])
def _devops_dr_snapshot():
    data = request.get_json(silent=True) or {}
    snap_type = data.get('type', 'incremental')
    try:
        from devops.disaster_recovery import global_disaster_recovery, SnapshotType
        st = SnapshotType.FULL if snap_type == 'full' else SnapshotType.INCREMENTAL
        snap = global_disaster_recovery.take_snapshot_now(st)
        return jsonify({
            'ok': bool(snap),
            'snapshot_id': snap.snapshot_id if snap else None,
            'size_kb': snap.size_bytes // 1024 if snap else 0,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/disaster_recovery/drill', methods=['POST'])
def _devops_dr_drill():
    try:
        from devops.disaster_recovery import global_disaster_recovery
        result = global_disaster_recovery.run_drill_now()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/disaster_recovery/stage_restore', methods=['POST'])
def _devops_dr_stage():
    data = request.get_json(silent=True) or {}
    snapshot_id = data.get('snapshot_id', '')
    operator    = data.get('operator', 'unknown')
    try:
        from devops.disaster_recovery import global_disaster_recovery
        job = global_disaster_recovery.stage_restore(snapshot_id, operator)
        return jsonify({
            'ok':         bool(job),
            'restore_id': job.restore_id if job else None,
            'status':     job.status.value if job else None,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/devops/disaster_recovery/confirm_restore', methods=['POST'])
def _devops_dr_confirm_restore():
    data = request.get_json(silent=True) or {}
    restore_id = data.get('restore_id', '')
    operator   = data.get('operator', 'unknown')
    try:
        from devops.disaster_recovery import global_disaster_recovery
        ok = global_disaster_recovery.confirm_restore(restore_id, operator)
        return jsonify({'ok': ok})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Cluster ───────────────────────────────────────────────────────────────────

@telemetry_bp.route('/api/cluster/topology')
def _cluster_topology():
    try:
        from cluster.control_plane import get_control_plane
        return jsonify(get_control_plane().snapshot())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/locks')
def _cluster_locks():
    try:
        from cluster.distributed_lock import get_lock_manager
        lm = get_lock_manager()
        return jsonify({
            'stats': lm.stats(),
            'active_locks': lm.active_lock_list(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/tasks')
def _cluster_tasks():
    try:
        from cluster.task_orchestrator import get_task_orchestrator
        to = get_task_orchestrator()
        return jsonify({
            'stats': to.stats(),
            'active_leases': to.active_lease_list(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/tasks/cancel', methods=['POST'])
def _cluster_task_cancel():
    data = request.get_json(silent=True) or {}
    task_id = data.get('task_id', '')
    reason  = data.get('reason', 'Operator cancellation')
    try:
        from cluster.task_orchestrator import get_task_orchestrator
        ok = get_task_orchestrator().cancel(task_id, reason)
        return jsonify({'ok': ok, 'task_id': task_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/events')
def _cluster_events():
    try:
        from cluster.event_fabric import get_event_fabric
        ef = get_event_fabric()
        return jsonify({
            'stats': ef.stats(),
            'channels': ef.channel_subscriber_counts(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/resources')
def _cluster_resources():
    try:
        from cluster.resource_governor import get_cluster_resource_governor
        return jsonify(get_cluster_resource_governor().cluster_resource_snapshot())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@telemetry_bp.route('/api/cluster/leader')
def _cluster_leader():
    try:
        from cluster.control_plane import get_control_plane
        return jsonify(get_control_plane().leader_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

