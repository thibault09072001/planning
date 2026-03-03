import streamlit as st
import pandas as pd
import io
import math
from ortools.sat.python import cp_model

# --- 1. CONFIGURATION DE L'INTERFACE (MODE PRO) ---
st.set_page_config(page_title="Système RH | Matrice EHPAD", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

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
    
    st.markdown("---")
    st.caption("🔒 Moteur de résolution v11.0 (Trame Générique)")
    st.caption("✓ Roulement Infini (Boucle temporel)\n✓ Format Jours (L,M,M...)\n✓ Contrats stricts (20j/16j)\n✓ Max 4j consécutifs\n✓ Coupés : 16x (1/1) + 2x (2/0)")

# --- 3. ESPACE CENTRAL (TABLEAU DE BORD) ---
st.title("Génération de la Trame de Roulement")
st.markdown("💡 *Matrice vierge perpétuelle. Si vous devez bloquer un jour, utilisez son numéro dans le cycle (ex: '1' pour le 1er Lundi, '28' pour le dernier Dimanche).*")

col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
with col_kpi1:
    st.metric(label="Jours de la matrice", value=nb_semaines * 7)
with col_kpi2:
    st.metric(label="Titulaires (WE)", value="9")
with col_kpi3:
    st.metric(label="Remplaçants (WE)", value="2")

st.markdown("<br>", unsafe_allow_html=True)

data_base = pd.DataFrame({
    "Nom": [f"Salarié {i+1}" for i in range(15)] + [f"Salarié {i+16}" for i in range(3)],
    "Contrat (%)": [100]*15 + [80]*3,
    "Absence (Jour 1 à 28)": [""] * 18
})

st.subheader("Registre du Personnel")
df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# L'extracteur d'absences est adapté pour lire des numéros de jours (1 à N) au lieu de dates
def extraire_indices_absences(texte, total_jours):
    indices = []
    if not texte or pd.isna(texte): return indices
    segments = str(texte).replace(' ', '').split(',')
    for segment in segments:
        try:
            if '-' in segment:
                d1_s, d2_s = segment.split('-')
                d1, d2 = int(d1_s) - 1, int(d2_s) - 1
                for i in range(d1, d2 + 1):
                    if 0 <= i < total_jours: indices.append(i)
            else:
                jour = int(segment) - 1
                if 0 <= jour < total_jours: indices.append(jour)
        except: continue
    return list(set(indices))

st.markdown("<br>", unsafe_allow_html=True)

# --- 4. MOTEUR DE RÉSOLUTION ---
if st.button("🚀 GÉNÉRER LA TRAME DE ROULEMENT", type="primary", use_container_width=True):
    
    df_equipe['Contrat (%)'] = pd.to_numeric(df_equipe['Contrat (%)'], errors='coerce')
    df_equipe = df_equipe.dropna(subset=['Nom', 'Contrat (%)'])
    
    noms_titulaires = df_equipe["Nom"].tolist()
    valeurs_contrats = df_equipe["Contrat (%)"].tolist()
    absences_declarees = df_equipe["Absence (Jour 1 à 28)"].tolist()
    
    noms_complets = noms_titulaires + ["REMPLAÇANT 1", "REMPLAÇANT 2"]
    nb_titulaires = len(noms_titulaires)
    total_effectif = len(noms_complets)
    
    with st.spinner("Création de la boucle temporelle parfaite (environ 45-60 secondes)..."):
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
        mult_cycle = nb_semaines // 4 
        
        # --- CONTRAINTES TITULAIRES ---
        for e in range(nb_titulaires):
            indices_abs = extraire_indices_absences(absences_declarees[e], jours_cycle)
            charge_max = int((valeurs_contrats[e] / 100) * 5 * nb_semaines)
            jours_a_deduire = int(round(len(indices_abs) * (5.0 / 7.0) * (valeurs_contrats[e] / 100.0)))
            cible_jours = charge_max - jours_a_deduire
            cibles_travail.append(cible_jours)
            
            model.Add(sum(x[(e, d, p)] for d in range(jours_cycle) for p in postes) == cible_jours)
            
            for d in range(jours_cycle): model.AddAtMostOne(x[(e, d, p)] for p in postes)
            
            # Repos Circulaire : Pas de A suivi de M (y compris fin -> début)
            for d in range(jours_cycle): 
                jour_suivant = (d + 1) % jours_cycle 
                model.AddImplication(x[(e, d, 'A')], x[(e, jour_suivant, 'M')].Not())
            
            # Max 4 Jours Circulaire 
            for d in range(jours_cycle): 
                model.Add(sum(x[(e, (d+i) % jours_cycle, p)] for i in range(5) for p in postes) <= 4)
            
            jours_semaine = [d for d in range(jours_cycle) if d % 7 < 5]
            jours_we = [d for d in range(jours_cycle) if d % 7 >= 5]
            c_sem_var = sum(x[(e, d, 'C')] for d in jours_semaine)
            c_we_var = sum(x[(e, d, 'C')] for d in jours_we)
            est_special = model.NewBoolVar(f'special_c_{e}')
            groupe_special_coupures.append(est_special)
            
            if mult_cycle >= 1:
                model.Add(c_sem_var == (1 * mult_cycle) + est_special)
                model.Add(c_we_var == (1 * mult_cycle) - est_special)
            
            indicateurs_we = []
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                actif_we = model.NewBoolVar(f'actif_we_{e}_{w}')
                model.Add(sum(x[(e, sat, p)] for p in postes) == sum(x[(e, sun, p)] for p in postes))
                model.AddMaxEquality(actif_we, [x[(e, sat, p)] for p in postes])
                indicateurs_we.append(actif_we)
                periode = range(w * 7, w * 7 + 7)
                model.Add(sum(x[(e, d, p)] for d in periode for p in postes) <= 4 + (2 * actif_we))

            # Week-ends Circulaires : Pas de WE en Sem 4 + WE en Sem 1
            for w in range(nb_semaines): 
                we_suivant = (w + 1) % nb_semaines 
                model.Add(indicateurs_we[w] + indicateurs_we[we_suivant] <= 1)
                
            for d in indices_abs:
                for p in postes: model.Add(x[(e, d, p)] == 0)

        if mult_cycle >= 1:
            model.Add(sum(groupe_special_coupures) == 2 * mult_cycle)

        # --- CONTRAINTES REMPLAÇANTS ---
        for e in range(nb_titulaires, total_effectif):
            cibles_travail.append("Remp.") 
            for d in range(jours_cycle):
                model.AddAtMostOne(x[(e, d, p)] for p in postes)
                if d % 7 < 5:
                    for p in postes: model.Add(x[(e, d, p)] == 0)
                if d % 7 == 5: 
                    model.Add(sum(x[(e, d, p)] for p in postes) == sum(x[(e, d+1, p)] for p in postes))
            for d in range(jours_cycle):
                model.Add(x[(e, d, 'C')] == 0)

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

        # --- OPTIMISATION ---
        poids_titulaire = sum(x[(e, d, p)] for e in range(nb_titulaires) for d in range(jours_cycle) for p in postes)
        poids_remplacant = sum(x[(e, d, p)] for e in range(nb_titulaires, total_effectif) for d in range(jours_cycle) for p in postes)
        model.Maximize(poids_titulaire * 10 - poids_remplacant)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 75.0 
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
                    for d in range(jours_cycle):
                        jour_suivant = (d + 1) % jours_cycle
                        if ligne_planning[d] == 'A' and ligne_planning[jour_suivant] == 'M':
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

            # --- EXPORT EXCEL PROFESSIONNEL (TRAME) ---
            colonnes = [f"J{i+1}" for i in range(jours_cycle)]
            df_final = pd.DataFrame(resultats, columns=colonnes, index=noms_utilises)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                # header=False pour dessiner nous-mêmes nos en-têtes complexes
                df_final.to_excel(writer, sheet_name='Trame', startrow=3, header=False)
                wb, ws = writer.book, writer.sheets['Trame']
                
                fmt_titre = wb.add_format({'bold': True, 'font_size': 16, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#2C3E50', 'font_color': 'white'})
                fmt_semaine = wb.add_format({'bold': True, 'bg_color': '#D5DBDB', 'border': 1, 'align': 'center'})
                fmt_jour = wb.add_format({'bold': True, 'bg_color': '#EAEDED', 'border': 1, 'align': 'center'})
                
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
                ws.set_column(1, jours_cycle, 8) 
                ws.set_column(jours_cycle + 1, jours_cycle + 1, 45) 
                
                # Figer les 3 premières lignes et la 1ère colonne
                ws.freeze_panes(3, 1) 
                
                # LIGNE 0 : Titre
                ws.merge_range(0, 0, 0, jours_cycle + 1, f"TRAME DE ROULEMENT CYCLIQUE - EHPAD", fmt_titre)
                
                # LIGNE 1 : Semaines (S1, S2...)
                ws.write(1, 0, "", fmt_semaine)
                for w in range(nb_semaines):
                    ws.merge_range(1, (w*7)+1, 1, (w*7)+7, f"SEMAINE {w+1}", fmt_semaine)
                ws.write(1, jours_cycle + 1, "", fmt_semaine)
                
                # LIGNE 2 : Jours (L, M, M...)
                ws.write(2, 0, "Employés", fmt_jour)
                lettres_jours = ["L", "M", "M", "J", "V", "S", "D"]
                for c_idx in range(jours_cycle):
                    ws.write(2, c_idx + 1, lettres_jours[c_idx % 7], fmt_jour)
                ws.write(2, jours_cycle + 1, "AUDIT DE TRAME", fmt_jour)
                
                # LIGNE 3+ : Données
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
                        ws.write(r_idx + 3, c_idx + 1, val, format_cible)
                        
                    ws.write(r_idx + 3, jours_cycle + 1, audit_data[r_idx], fmt_audit)
            
            st.success("✅ Trame générée ! Le document ne comporte plus de dates, juste S1, S2 et les jours L,M,M...")
            st.download_button("📥 TÉLÉCHARGER LA MATRICE (EXCEL)", buffer.getvalue(), f"Matrice_Roulement_EHPAD.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        else:
            st.error("❌ Impossible de générer la trame. Vérifiez que rien ne bloque l'équilibre strict des postes.")
