import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION DE L'INTERFACE (MODE PRO) ---
st.set_page_config(page_title="Système RH | Planning EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
div.stDownloadButton > button {
    background-color: #28a745 !important;
    color: white !important;
    font-size: 18px !important;
    font-weight: bold !important;
    padding: 15px 30px !important;
    border-radius: 8px !important;
    border: none !important;
    width: 100% !important;
}
div.stDownloadButton > button:hover {
    background-color: #218838 !important;
    border-color: #1e7e34 !important;
}
</style>
""", unsafe_allow_html=True)

# --- 2. MENU LATÉRAL (SIDEBAR) ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=60) 
    st.title("Configuration")
    st.markdown("---")
    
    nb_semaines = st.number_input("⏱️ Durée du cycle (semaines)", min_value=4, max_value=12, value=4, step=4)
    
    aujourdhui = datetime.today()
    lundi_par_defaut = aujourdhui - timedelta(days=aujourdhui.weekday())
    date_debut = st.date_input("📅 Date d'effet (Lundi)", value=lundi_par_defaut)
    
    if date_debut.weekday() != 0:
        st.error("🛑 La date d'effet doit obligatoirement être un Lundi.")
        st.stop()
        
    st.markdown("---")
    st.caption("🔒 Moteur de résolution v9.0 (Ultra Strict)")
    st.caption("✓ Contrats stricts (20j/16j)\n✓ Max 4j consécutifs (Rétabli)\n✓ 9 Titulaires / 2 Remplaçants WE\n✓ Zéro remplaçant en semaine\n✓ Coupés : 16x (1 Sem/1 WE) + 2x (2 Sem/0 WE)")

# --- 3. ESPACE CENTRAL (TABLEAU DE BORD) ---
st.title("Génération du Planning Opérationnel")
st.markdown("Veuillez vérifier les absences et les quotités de travail avant de lancer le calcul.")

col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
with col_kpi1:
    st.metric(label="Jours planifiés", value=nb_semaines * 7)
with col_kpi2:
    st.metric(label="Titulaires requis le Week-end", value="9")
with col_kpi3:
    st.metric(label="Remplaçants requis le Week-end", value="2")

st.markdown("<br>", unsafe_allow_html=True)

data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3,
    "Congés / Absences": [""] * 18
})

st.subheader("Registre du Personnel")
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

def extraire_indices_absences(texte, date_ref, total_jours):
    indices = []
    if not texte or pd.isna(texte): return indices
    segments = str(texte).replace(' ', '').split(',')
    for segment in segments:
        try:
            if '-' in segment:
                d1_s, d2_s = segment.split('-')
                d1 = datetime.strptime(d1_s + f"/{date_ref.year}", "%d/%m/%Y").date()
                d2 = datetime.strptime(d2_s + f"/{date_ref.year}", "%d/%m/%Y").date()
                for i in range((d2 - d1).days + 1):
                    ecart = (d1 + timedelta(days=i) - date_ref).days
                    if 0 <= ecart < total_jours: indices.append(ecart)
            else:
                cible = datetime.strptime(segment + f"/{date_ref.year}", "%d/%m/%Y").date()
                ecart = (cible - date_ref).days
                if 0 <= ecart < total_jours: indices.append(ecart)
        except: continue
    return list(set(indices))

st.markdown("<br>", unsafe_allow_html=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("🚀 LANCER L'OPTIMISATION DU PLANNING", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    absences_declarees = df_equipe["Congés / Absences"].tolist()
    
    noms_complets = noms_titulaires + ["REMPLAÇANT 1", "REMPLAÇANT 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Analyse de l'équité des horaires et génération (environ 45-60 secondes)..."):
        jours_cycle = nb_semaines * 7
        postes = ['M', 'A', 'C']
        model = cp_model.CpModel()
        x = {}
        for e in range(total_effectif):
            for d in range(jours_cycle):
                for p in postes:
                    x[(e, d, p)] = model.NewBoolVar(f'staff_{e}_{d}_{p}')
        
        cibles_travail = [] 
        groupe_special_coupures = []
        mult_cycle = nb_semaines // 4 # Multiplicateur (1 pour 4 semaines, 2 pour 8 semaines...)
        
        # --- CONTRAINTES TITULAIRES ---
        for e in range(nb_titulaires):
            indices_abs = extraire_indices_absences(absences_declarees[e], date_debut, jours_cycle)
            charge_max = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            jours_a_deduire = int(round(len(indices_abs) * (5.0 / 7.0) * (valeurs_contrats[e] / 100.0)))
            cible_jours = charge_max - jours_a_deduire
            cibles_travail.append(cible_jours)
            
            # Contrat au jour près
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            for d in range(jours_cycle - 1): model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
            
            # 🛑 Règle des 4 jours consécutifs max (RÉTABLIE)
            for d in range(jours_cycle - 4): 
                model.Add(sum(x[(e, d+i, p)] for i in range(5) for p in postes) <= 4)
            
            # 🛑 RÉPARTITION STRICTE ET DÉFINITIVE DES HORAIRES COUPÉS ('C')
            jours_semaine = [d for d in range(jours_cycle) if d % 7 < 5]
            jours_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            
            c_sem_var = sum(x[(e, d, 'C')] for d in jours_semaine)
            c_we_var = sum(x[(e, d, 'C')] for d in jours_we)
            
            est_special = model.NewBoolVar(f'special_c_{e}')
            groupe_special_coupures.append(est_special)
            
            # Égalité Stricte : L'IA n'a plus le choix.
            if mult_cycle >= 1:
                model.Add(c_sem_var == (1 * mult_cycle) + est_special)
                model.Add(c_we_var == (1 * mult_cycle) - est_special)
            
            indicateurs_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif_we = model.NewBoolVar(f'actif_we_{e}_{w}')
                for p in postes: model.Add(x[(e, sat, p)] == x[(e, sun, p)]) 
                model.AddMaxEquality(actif_we, [x[(e, sat, p)] for p in postes])
                indicateurs_we.append(actif_we)
                periode = range(w * 7, w * 7 + 7)
                model.Add(sum(x[(e, d, p)] for d in periode for p in postes) <= 4 + (2 * actif_we))

            for w in range(nb_semaines - 1): model.Add(indicateurs_we[w] + indicateurs_we[w+1] <= 1)
            for d in indices_abs:
                for p in postes: model.Add(x[(e, d, p)] == 0)

        # 🛑 On force exactement 2 personnes (par bloc de 4 semaines) à être "Spéciales" (2 Sem / 0 WE)
        if mult_cycle >= 1:
            model.Add(sum(groupe_special_coupures) == 2 * mult_cycle)

        # --- CONTRAINTES REMPLAÇANTS ---
        for e in range(nb_titulaires, total_effectif):
            cibles_travail.append("Remp.") 
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 < 5:
                    for p in postes: model.Add(x[(e, d, p)] == 0) # Zéro en semaine
                if d % 7 == 5: 
                    for p in postes: model.Add(x[(e, d, p)] == x[(e, d+1, p)])

        # --- QUOTAS DE SERVICE ---
        for d in range(jours_cycle):
            is_we = (d % 7 >= 5)
            m_target, a_target, c_target = (6, 3, 2) if is_we else (8, 4, 1)
            
            model.Add(sum(x[(e, d, 'M')] for e in range(total_effectif)) >= m_target) 
            model.Add(sum(x[(e, d, 'A')] for e in range(total_effectif)) == a_target)
            model.Add(sum(x[(e, d, 'C')] for e in range(total_effectif)) == c_target)
            
            if is_we:
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires) for p in postes) == 9)
                model.Add(sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for p in postes) == 2)

        # --- OPTIMISATION GLOBALE ---
        poids_titulaire = sum(x[(e, d, p)] for e in range(nb_titulaires) for d in range(jours_cycle) for p in postes)
        poids_remplacant = sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for d in range(jours_cycle) for p in postes)
        model.Maximize(poids_titulaire * 10 - poids_remplacant)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 75.0 # Un peu plus de temps pour l'équation exacte
        statut = solver.Solve(model)

        if statut in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultats, noms_utilises, audit_data = [], [], []
            for e in range(total_effectif):
                total_activite = sum(solver.Value(x[(e, d, p)]) for d in range(jours_cycle) for p in postes)
                if e < nb_titulaires or total_activite > 0:
                    ligne_planning = []
                    jours_travailles = 0
                    c_semaine = 0
                    c_we = 0
                    
                    for d in range(jours_cycle):
                        valeur = "Repos"
                        for p in postes:
                            if solver.Value(x[(e, d, p)]) == 1: 
                                valeur = p
                                jours_travailles += 1
                                if p == 'C':
                                    if d % 7 < 5: c_semaine += 1
                                    else: c_we += 1
                        ligne_planning.append(valeur)
                    
                    enchainements_am = 0
                    for d in range(jours_cycle - 1):
                        if ligne_planning[d] == 'A' and ligne_planning[d+1] == 'M':
                            enchainements_am += 1
                            
                    resultats.append(ligne_planning)
                    noms_utilises.append(noms_complets[e])
                    
                    if e < nb_titulaires:
                        etoile = "🌟 " if c_semaine > c_we else "✅ "
                        audit_txt = f"{etoile}{jours_travailles}j/{cibles_travail[e]}j | {c_semaine}C Sem, {c_we}C WE | {enchainements_am} A->M"
                        if jours_travailles != cibles_travail[e]: audit_txt = "❌ ERREUR CONTRAT"
                    else:
                        audit_txt = f"{jours_travailles}j WE | {c_semaine}C Sem, {c_we}C WE"
                    
                    audit_data.append(audit_txt)

            colonnes = [(date_debut + timedelta(days=i)).strftime('%A %d/%m').capitalize() for i in range(jours_cycle)]
            df_final = pd.DataFrame(resultats, columns=colonnes, index=noms_utilises)
            df_final['AUDIT RH'] = audit_data 
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name='Planning', startrow=1)
                wb, ws = writer.book, writer.sheets['Planning']
                
                fmt_titre = wb.add_format({'bold': True, 'font_size': 16, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#2C3E50', 'font_color': 'white'})
                fmt_header = wb.add_format({'bold': True, 'bg_color': '#EAEDED', 'border': 1, 'align': 'center'})
                fmt_m = wb.add_format({'bg_color': '#E0F2F1', 'font_color': '#00695C', 'align': 'center', 'border': 1})
                fmt_a = wb.add_format({'bg_color': '#FFF3E0', 'font_color': '#E65100', 'align': 'center', 'border': 1})
                fmt_c = wb.add_format({'bg_color': '#FFEBEE', 'font_color': '#B71C1C', 'align': 'center', 'border': 1})
                fmt_remp = wb.add_format({'bg_color': '#34495E', 'font_color': '#FFFFFF', 'bold': True, 'align': 'center', 'border': 1})
                fmt_we = wb.add_format({'bg_color': '#F8F9F9', 'border': 1})
                fmt_repos = wb.add_format({'font_color': '#BDC3C7', 'align': 'center', 'border': 1})
                fmt_audit = wb.add_format({'font_color': '#34495E', 'bold': True, 'align': 'left', 'border': 1, 'bg_color': '#F4F6F6'}) 
                
                ws.set_default_row(22)
                ws.set_row(0, 35) 
                ws.set_column('A:A', 25) 
                ws.set_column(1, jours_cycle, 13) 
                ws.set_column(jours_cycle + 1, jours_cycle + 1, 45) 
                ws.freeze_panes(2, 1) 
                
                ws.merge_range(0, 0, 0, jours_cycle + 1, f"PLANNING OPÉRATIONNEL - CYCLE DÉBUTANT LE {date_debut.strftime('%d/%m/%Y')}", fmt_titre)
                
                ws.write(1, 0, "Employés", fmt_header)
                for i, col_name in enumerate(df_final.columns):
                    ws.write(1, i + 1, col_name, fmt_header)
                
                for r_idx in range(len(noms_utilises)):
                    est_remplacant = "REMPLAÇANT" in noms_utilises[r_idx]
                    for c_idx in range(jours_cycle):
                        val = df_final.iloc[r_idx, c_idx]
                        if est_remplacant and val != "Repos": format_cible = fmt_remp
                        elif val == 'M': format_cible = fmt_m
                        elif val == 'A': format_cible = fmt_a
                        elif val == 'C': format_cible = fmt_c
                        elif c_idx % 7 >= 5: format_cible = fmt_we
                        else: format_cible = fmt_repos
                        ws.write(r_idx + 2, c_idx + 1, val, format_cible)
                        
                    ws.write(r_idx + 2, jours_cycle + 1, df_final.iloc[r_idx, jours_cycle], fmt_audit)
            
            st.success("✅ Fichier Excel généré avec succès !")
            st.download_button("📥 TÉLÉCHARGER LE PLANNING", buffer.getvalue(), f"Planning_Direction_{date_debut.strftime('%d-%m-%Y')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        else:
            st.error("❌ Impossible de trouver une solution mathématique stricte. S'il y a des absences déclarées, vérifiez qu'elles ne bloquent pas l'obligation pour chaque salarié de faire ses 1 ou 2 coupés.")
