/* Réponses de l'API utilisées par l'interface (voir coach/web/service.py, onboarding_flow.py, career_flow.py). */

export interface Me {
  today: string;
  onboarding: boolean;
  first_name: string;
  google: boolean;
  strava: boolean;
  push: boolean;
  budget_eur: number;
  spent_today_eur: number;
  race_name: string | null;
  race_date: string | null;
  days_to_race: number | null;
  push_public_key: string | null;
  /** v0.36 : coach choisi (ton des messages, avatar) */
  coach?: CoachPersona;
}

export type CoachGender = "homme" | "femme";
export type CoachPersonality = "bienveillant" | "exigeant" | "motivateur" | "sante";
export type CoachAddress = "tu" | "vous";
export interface CoachPersona {
  gender: CoachGender;
  personality: CoachPersonality;
  name: string;
  address: CoachAddress;
  /** false tant que l'athlète n'a pas choisi (valeurs par défaut) */
  chosen?: boolean;
  /** v0.48 : simple = termes techniques expliqués (titres en clair, « C'est quoi ? ») */
  vocabulary?: "simple" | "expert";
}
/** v0.48 : terme technique d'une séance et son explication en langage courant */
export interface Term { term: string; text: string }

/** v0.42 : séance validée automatiquement par une sortie synchronisée (ressenti à compléter) */
export interface SessionTypeCheck { verdict: "conforme" | "origine" | "plus_facile" | "plus_dur" | "autre" | "non_verifie"; ok: boolean; detected: string; label: string; text: string }
export interface AutoValidation { activity: string; start: string; day?: string; minutes: number; km: number; ratio: number; status: string;
  /** v0.44 : lien posé à la main / confirmé par le ressenti */ manual?: boolean; confirmed?: boolean;
  /** v0.43 : type de séance couru comparé au type prévu */ type?: SessionTypeCheck | null }
export interface Feedback { status: "done" | "modified" | "skipped"; rpe: number | null; note: string; auto?: AutoValidation }

export interface Session {
  session_id: string;
  day: string;
  intensity: string;
  kind: string;
  title: string;
  detail: string;
  duration_min: number;
  elevation_gain_m: number;
  planned_load?: number;
  is_race?: boolean;
  key: boolean;
  optional?: boolean;
  /** v0.50.1 : séance transformée par le coach : titre prévu au départ */
  orig_title?: string;
  /** v0.48 : vocabulaire simple : termes techniques expliqués */
  terms?: Term[];
  /** v0.50 : FC cible (bpm), allure si connue, effort ; `lead` = ce qui fait foi en premier */
  target?: SessionTarget | null;
  feedback: Feedback | null;
  /** v0.38 : durée réelle (sortie du jour la plus longue), bilan nutrition proposé (≥ 1 h 30), bilan saisi */
  actual_min?: number | null;
  nutrition_eligible?: boolean;
  nutrition?: NutritionLog | null;
}

/** v0.38 : bilan nutrition d'une sortie longue (clé `nutrition_logs` du serveur) */
export interface NutritionItem { id: string; qty: number; carbs_g: number | null }
export interface NutritionLog {
  date: string; session_id: string | null; activity_id: string | null; duration_h: number;
  items: NutritionItem[]; water_ml: number; carbs_g_h: number; sodium_mg_h: number; fluid_ml_h: number;
  digestion: number; symptoms: string[]; comment: string;
}
export interface Food {
  id: string; name: string; family: string; portion: string; carbs_g: number; sodium_mg: number; fluid_ml: number;
  caffeine_mg: number; fructose: boolean; digest: string; note: string;
}
export interface NutritionFoods {
  foods: Food[]; families: { id: string; label: string }[]; symptoms: { id: string; label: string }[];
  digestion: { v: number; label: string }[]; trained_max_g_h: number | null; min_duration_min: number;
}
export interface NutritionSummary {
  trained_max_g_h: number | null; tolerated: string[]; avoid: string[]; count: number; target_long_g_h?: [number, number];
  labels?: Record<string, string>; symptom_labels?: Record<string, string>;
  last: { date: string; session_id: string | null; duration_h: number; carbs_g_h: number; sodium_mg_h: number; fluid_ml_h: number; digestion: number; symptoms: string[] }[];
}
export interface NutritionSaveResponse { log: NutritionLog; advice: string[]; summary: NutritionSummary; messages: ChatMessage[]; pending: boolean; typing: boolean }

export interface Activity {
  day: string;
  start: string;
  sport: string;
  name: string;
  minutes: number;
  km: number;
  dplus: number;
  avg_hr: number | null;
  load: number;
  load_source?: string;
  /** v0.48 : durée invraisemblable (hors statistiques) ; temps écoulé brut quand la durée retenue en diffère */
  suspect?: boolean;
  elapsed_min?: number;
}

export interface LoadState {
  ctl: number | null;
  atl: number | null;
  tsb: number | null;
  acwr: number | null;
  history_days: number;
  last_sync: string | null;
}

export interface TodayView extends Me {
  /** v0.52.3 : synchro Strava lancée à l'ouverture : la vue se recharge quand elle est finie */
  syncing?: boolean;
  sessions: Session[];
  activities: Activity[];
  state: LoadState;
  focus: string | null;
  coach_note: string | null;
  next: Session | null;
}

/** v0.38 : alerte du jour (deux séances dures le même jour), toujours avec un libellé */
export interface DayAlert { kind: "two_hard"; label: string; sessions: string[] }
export interface WeekDay { date: string; sessions: Session[]; activities: Activity[]; alert?: DayAlert | null }
export interface WeekView {
  start: string;
  today: string;
  days: WeekDay[];
  planned_min: number;
  done_min: number;
  phase: string | null;
  focus: string | null;
  review: string | null;
  reviewed_at: string | null;
  keys: { day: string; text: string }[];
  /** v0.50 : premier jour de la semaine de l'athlète (0 = lundi … 6 = dimanche) */
  week_start?: number;
  /** v0.50 : dernier récap posté (début de semaine ISO) */
  recap?: { last: string | null; count: number };
}

export interface AgreedPlan {
  adopted_at: string;
  first_day: string;
  last_day: string;
  sessions: number;
  plan_text?: string;
}
export interface Memory { commitments: string[]; agreed_plan: AgreedPlan | null; has_plan: boolean; versions: number }
export interface StateView extends LoadState { memory: Memory; activities: Activity[]; goals?: GoalItem[]; nutrition?: NutritionSummary }

/** v0.37 : un objectif (A = principal, B = secondaire, C = projet), liste déjà classée par le serveur */
export interface GoalItem {
  role: "A" | "B" | "C";
  label: string;
  name: string;
  date: string | null;
  year: number | null;
  days_left: number | null;
  km: number | null;
  dplus: number | null;
  kind: string;
  note: string;
  done: boolean;
  estimate?: [number, number, number] | null;
}

/** v0.37 : réponse de POST /api/sessions/{id}/move */
export interface MoveResponse {
  moved: boolean;
  from?: string;
  to?: string;
  session: Session;
  warnings: string[];
  /** v0.38 : avertissements détaillés (jamais de refus pour raison d'entraînement) */
  alerts?: { text: string; level: "warn" | "strong"; would_block: boolean; rule?: string }[];
  messages: ChatMessage[];
  pending: boolean;
  /** v0.38 : le coach écrit sa réaction en tâche de fond */
  typing?: boolean;
  calendar_changed: boolean;
}

/** v0.40 : un agent de l'équipe consulté pour ce message (outils appelés, risque, veto, fiches, coût). */
export interface AgentTrace {
  name: "charge" | "nutrition" | "course" | "prevention";
  label: string; icon: string; reason?: string;
  tools: { name: string; ms: number }[];
  risks?: string[]; risk_level: "low" | "medium" | "high"; veto: boolean; veto_reason?: string | null;
  evidence_ids: string[]; cost_eur: number; ms: number; degraded?: boolean;
  findings?: string[];
}
export interface CriticTrace { verdict: "ok" | "revise"; reasons: string[]; revised: boolean; rounds?: number; judge?: string }
export interface RoutingTrace { specialists: string[]; reason: string; by: string; ms?: number }
export interface ChatMeta {
  trace?: boolean | string; nodes?: string[]; latency_s?: number; cost_eur?: number; calendar_ops?: number;
  agents?: AgentTrace[]; critic?: CriticTrace; routing?: RoutingTrace; cost_by_agent?: Record<string, number>;
  /** v0.40 : message « l'Œil du coach est prêt » */
  eye?: boolean; degraded?: string; fallback?: boolean;
  /** v0.48 : détail court de l'erreur Claude (nœud, modèle, type, message nettoyé) */
  degraded_detail?: string;
}

/** v0.40 : rapport « Œil du coach » */
export interface EyePriority { action: string; why: string; cards: string[] }
export interface EyeReport {
  summary: string; strengths: string[]; weaknesses: string[]; risks: string[]; nutrition: string; race: string;
  priorities: EyePriority[]; next_review: string; to_measure?: string[];
}
export interface EyeRecord { at: string; day: string; report: EyeReport; agents: AgentTrace[]; critic: CriticTrace; cost_eur: number; latency_s: number; degraded?: string | null; free?: boolean }
export interface EyeStatus {
  required_level: number; level: number; unlocked: boolean; /** v0.41 : première analyse offerte */ free_available?: boolean; xp: number; xp_required: number; progress_pct: number;
  cooldown_days: number; running: boolean; next_at: string | null; available: boolean; last?: EyeRecord | null;
  started?: boolean; typing?: boolean;
  /** analyses précédentes (la dernière en fin de liste) */
  history?: { day: string; summary: string }[];
}
export interface ChatMessage { id?: number; role: string; kind: string; text: string; at: string; meta?: ChatMeta | null }
export interface ChatResponse {
  messages: ChatMessage[];
  pending: boolean;
  quick?: string[];
  calendar_changed?: boolean;
  /** v0.38 : GET /api/chat — le coach écrit (réaction en tâche de fond) */
  typing?: boolean;
}

export interface FeedbackResponse { feedback: Feedback; messages: ChatMessage[]; coach_error?: string }
export interface UploadResponse { status: "new" | "upgraded" | "duplicate"; activity: Activity }

export interface About {
  version?: string;
  registry?: string;
  registry_hash?: string;
  cards?: number;
  today?: string;
  llm?: string;
  dictation?: { server?: boolean; ready?: boolean; model?: string; loading?: boolean };
  image?: { enabled?: boolean; model?: string | null; per_day?: number; today?: number };
  /** v0.40 : mode d'agents et composition de l'équipe */
  agents?: AgentsInfo;
}
export interface AgentRole { key: string; label: string; icon: string; tier: "small" | "main"; model: string; role: string; tools: string[] }
export interface AgentsInfo { mode: "multi" | "single"; team: AgentRole[]; all: AgentRole[]; note: string; eye_level: number; eye_every_days: number }

export interface Onboarding {
  step: "connect" | "syncing" | "chat" | "done";
  activities?: number;
  strava?: boolean;
  strava_connected?: boolean;
  job?: { message?: string; error?: string } | null;
  connect?: string | null;
  connect_error?: string | null;
}
export interface StravaConnect { status: "redirect" | "connected" | "error" | string; url?: string; message?: string }

/* ---- progrès */
export type Point = [string, number | null];
export interface Goal {
  name: string;
  role?: "A" | "B" | "C";
  label?: string;
  date: string | null;
  km: number | null;
  dplus: number | null;
  days_to: number | null;
  estimate: [number, number, number] | null;
  delta_28d: number | null;
}
export interface RaceForm { day: string; tsb: number; ctl: number; label: string }
export interface Week {
  start: string; km: number; hours: number; dplus: number; load: number; monotony: number | null;
  planned: number; done: number; partial: boolean;
}
export type Estimate = { day: string } & Record<string, number | [number, number, number] | string | undefined>;
export interface EstimatesNow {
  ref: { name: string; day: string; km: number };
  marathon_gap: number;
  [k: string]: number | { name: string; day: string; km: number };
}
export interface ProfileAxis {
  key: string; label: string; score: number | null; score_3m: number | null;
  value?: string | null; basis?: string | null; confidence?: string; tip: string;
}
export interface Profile {
  axes: ProfileAxis[];
  not_measured: { key: string; label: string; why: string; how: string }[];
  strongest: string | null;
  weakest: string | null;
}
export interface ProgressView {
  empty?: boolean;
  now: { ctl: number | null; atl: number | null; tsb: number | null; tsb_label: string; km7: number; h7: number; dplus7: number };
  pmc: [string, number | null, number | null, number | null][];
  projection: [string, number | null, number | null, number | null][];
  race_form: RaceForm | null;
  rolling7: [string, number, number, number, number][];
  weeks: Week[];
  estimates: Estimate[];
  estimates_now: EstimatesNow | null;
  goals?: Goal[];
  goals_without_distance?: string[];
  my_goals?: GoalItem[];
  efficiency: [string, number, number][];
  efficiency_hr: number;
  profile: Profile | null;
}
export interface ActivitiesPage { activities: Activity[]; sports: string[]; total: number; next_offset: number | null }

/* ---- carrière */
export interface Trophy {
  id: string; family: string; label: string; desc: string; xp: number; tier: 1 | 2 | 3 | 4;
  day?: string | null; progress?: string | null;
}
export interface GearItem { id: string; level?: number | null; label: string; unlocked: boolean; how: string; seasonal?: boolean; in_season?: boolean }
/** v0.33 : événement du calendrier en cours ou proche (Halloween, Noël, anniversaire…) */
export interface CareerEvent {
  id: string; name: string; label: string; desc: string; xp: number; start: string; end: string;
  days_left: number; live: boolean; done: boolean; count: number; need: number; earned: boolean;
}
export interface SceneItem { id: string; label: string; unlocked: boolean; how: string }
export interface AvatarCfg {
  skin: number; hair: string; hair_color: number; beard: boolean; shirt: number; shorts: number; shoes: number;
  gear: string[] | null; scene: string | null;
  /** v0.47 : lieu et ambiance choisis, null = automatique (« aucune » = pas d'ambiance) */
  place?: string | null; ambiance?: string | null;
}
export interface AvatarImage {
  enabled: boolean; ready: boolean; current?: boolean; generating: boolean; error: string | null;
  url: string; every_h?: number; next_at: string | null; face?: boolean;
}
export interface StrengthEntry { day: string; exercise: string; kg: number; reps: number; bodyweight: number; one_rm: number }
export interface Career {
  /** v0.40 : niveau qui débloque l'Œil du coach */
  eye_level?: number;
  level: number; rank: string; xp: number; next_at: number; pct: number;
  trophies: Trophy[];
  families: Record<string, string>;
  timeline: { day: string; label: string; xp: number }[];
  levels: { day: string; level: number; rank: string }[];
  gear: GearItem[];
  scene: string;
  auto_scene?: string;
  scenes?: SceneItem[];
  /** v0.47 : décor en deux choix qui se croisent (lieu × ambiance) ; valeurs affichées, auto remplacé */
  place?: string; ambiance?: string; auto_place?: string; auto_ambiance?: string;
  places?: SceneItem[]; ambiances?: SceneItem[];
  count: number; total: number;
  next_levels: { level: number; at: number }[];
  new?: Trophy[];
  level_up: boolean;
  first_visit: boolean;
  avatar: AvatarCfg;
  avatar_set: boolean;
  strength_log?: StrengthEntry[];
  discipline: string | null;
  avatar_image?: AvatarImage;
  events?: CareerEvent[];
  face_level?: number;
  birthday?: string | null;
}

/* ---------------------------------------------------------------- v0.38 : simulateur de course (GET /api/race/sim) */
export type ScenarioId = "ambitieux" | "cible" | "prudent";
export type Heat = "fraiche" | "normale" | "chaude";
export interface SimArrival { s: number; clock: string }
export interface SimCutoff {
  limit_s: number; clock: string; label: string; approx: boolean; edition: number | null;
  margin_min: Record<ScenarioId, number>; status: "ok" | "juste" | "danger";
}
export interface SimPoint {
  name: string; km: number; km_official: number; alt: number | null; alt_approx: boolean;
  kind: "depart" | "arrivee" | "ravito" | "eau" | "repere"; services: string[]; note: string; estimate: boolean;
  snapped: boolean; stop_min: number; arrival: Record<ScenarioId, SimArrival>; cutoff: SimCutoff | null;
}
export interface SimTerrain { kind: "montee" | "roulant" | "descente"; km: number; time_s: number; pace_s_km: number; vam_m_h: number | null }
export interface SimSection {
  i: number; from: string; to: string; km0: number; km1: number; km: number; dplus: number; dminus: number;
  kind: "montee" | "descente" | "roulant"; kind_label: string; time_s: Record<ScenarioId, number>;
  pace_s_km: number | null; vam_m_h: number | null; terrain: SimTerrain[]; daylight: string; effort: string;
}
export interface SimClimb { name: string; km0: number; km1: number; gain_m: number; net_m: number; grade_pct: number; time_s: number; vam_m_h: number | null }
export interface RaceFoodItem { id: string; name: string; qty: number; carbs_g: number; sodium_mg: number; fluid_ml: number; caffeine_mg: number; portion?: string }
export interface RaceLeg {
  from: string; to: string; km0: number; km1: number; km: number; duration_s: number; start_s: number;
  carbs_g: number; carbs_g_h: number; fluid_ml: number; flasks: number; water_flasks: number; sodium_mg: number;
  caffeine_mg: number; at_aid: RaceFoodItem[]; carry: RaceFoodItem[]; at_aid_label: string; carry_label: string;
  late: boolean; notes: string[]; line: string;
}
export interface RaceNutrition {
  target_g_h: number; range_g_h: [number, number]; trained_max_g_h: number | null; rehearsal_needed: boolean;
  fluid_ml_h: number; sodium_mg_h: number; heat: Heat; notes: string[]; caffeine_note: string; heat_note: string;
  mandatory: string[]; avoid: string[]; tolerated: string[]; legs: RaceLeg[];
  totals: { carbs_g: number; fluid_ml: number; sodium_mg: number; caffeine_mg: number }; aid_food_label: string;
}
export interface RaceSim {
  available: boolean;
  reason?: string;
  need?: "distance" | "reference";
  goal?: { name: string; date: string | null; km: number | null; dplus: number | null; days_left: number | null; done?: boolean };
  mode?: "gpx" | "approx";
  mode_note?: string;
  gpx_error?: string | null;
  headline?: { finish_s: number; finish_clock: string; lo_s: number; lo_clock: string; hi_s: number; hi_clock: string; text: string };
  scenarios?: { id: ScenarioId; label: string; finish_s: number; finish_clock: string; itra_equiv: number | null }[];
  points?: SimPoint[];
  sections?: SimSection[];
  climbs?: SimClimb[];
  profile?: [number, number][];
  total_km?: number;
  daylight?: { dawn: string | null; sunrise: string | null; sunset: string | null; dusk: string | null; note: string };
  model_notes?: string[];
  conditions?: { heat: Heat; form: number; target_s: number | null };
  compute_ms?: number;
  course?: {
    name: string; official_km: number | null; official_dplus: number | null; start_local: string; start_place: string;
    time_limit_h: number | null; time_limit_note: string; cutoff_note: string; altitude_note: string;
    track_url: string | null; track_note: string; sources: { what: string; url?: string; edition: number; checked: string }[];
    generic: boolean; start_note: string; terrain: { paths: number; tracks: number; road: number; source: string } | null;
    aid_food: string; mandatory: string[]; manual_points: { name: string; km: number }[];
  };
  gpx?: { uploaded: string; km: number; dplus: number; warning: string; issues: string[]; name: string } | null;
  anchor?: { source: string; lo_s: number; median_s: number; hi_s: number; itra_effective: number | null; itra_range: [number, number] | null; k_h: number | null };
  nutrition?: RaceNutrition;
}

/** v0.44 : sorties qu'on peut lier à une séance (± 3 jours) */
export interface LinkCandidate extends AutoValidation { day: string; linked_to: string | null; linked_title: string | null; current: boolean }
export interface LinkCandidates { session_id: string; day: string; title: string; candidates: LinkCandidate[] }

/** v0.48 : « Tester Claude » (Réglages › Application) */
export interface ClaudeCheckStep { step: string; ok: boolean; model: string | null; ms?: number; message: string; detail?: string }
export interface ClaudeCheck { ok: boolean; steps: ClaudeCheckStep[]; cost_eur?: number }

/* ---------------------------------------------------------------- v0.49 : volume maximum par semaine (GET/POST /api/week-max) */
export interface WeekMaxView {
  minutes: number | null; km: number | null; dplus: number | null; inferred: boolean; text: string;
  best_4w_min: number | null; has_profile: boolean;
}
export interface WeekMaxSaved { ok: boolean; text: string; replan: boolean; messages: ChatMessage[]; quick?: string[] }

/* ---------------------------------------------------------------- v0.50 : FC cible, zones, semaine, récap */
export interface SessionTarget {
  zone: string; hr: [number, number]; hr_text: string; pace: string | null; rpe: string;
  lead: "hr" | "pace" | "effort"; short_reps: boolean; note: string; text: string;
}
export interface ZoneValue { value: number; source: string; source_id: "saisie" | "strava" | "estimee" | "reglage"; override: number | null; auto: number; auto_source: string }
export interface HrZone { zone: string; label: string; lo: number | null; hi: number | null; pct: string }
export interface ZonesView {
  hr_max: ZoneValue; hr_rest: ZoneValue; lthr: ZoneValue; zones: HrZone[];
  paces: { key: string; label: string; value: string }[];
  targets: (SessionTarget & { intensity: string; label: string })[];
  strava: { connected: boolean; api: boolean; scope_ok: boolean; needs_reconnect: boolean; zones: { min: number; max: number }[] | null; at: string | null; custom: boolean | null };
  model: string;
}
export interface WeekStartView { day: number; label: string; end_label: string; days: string[] }
export interface RecapSection { id: string; title: string; lines: string[] }
/** v0.51 : ressenti de fin de semaine (1 à 5) */
export interface WeekFeeling { fatigue: number; difficulty: number; fun: number; note: string; at?: string }
export interface RecapItem { week_start: string; week_end: string; text: string; sections: RecapSection[]; at?: string; data?: Record<string, unknown>; feeling?: WeekFeeling | null }
export interface RecapView { last: RecapItem | null; preview: RecapItem | null }
