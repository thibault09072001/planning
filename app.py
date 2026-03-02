import streamlit as st
import pandas as pd
import io
from ortools.sat.python import cp_model
from datetime import datetime, timedelta

# --- 1. CONFIGURATION DE LA PAGE WEB ---
st.set_page_config(page_title="Générateur Planning EHPAD", page_icon="🏥", layout="wide")
st.title("🏥 Générateur de Planning Soignants (IA)")
st.markdown("Outil de planification sous contraintes strictes. **Zéro enchaînement Après-midi -> Matin garanti.**")

# --- 2. INTERFACE UTILISATEUR (ENTRÉES) ---
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Paramètres")
    nb_semaines = st.number_input("Nombre de semaines :", min_value=2, max_value=12, value=4)
    date_debut = st.date_input("Date de début (Lundi de préférence) :", value=datetime(2026, 3, 2))

with col2:
    st.subheader("Équipe Soignante")
    st.markdown("Tu peux ajouter, modifier ou supprimer des soignants ici :")
    # Tableau dynamique de base
    data_base = pd.DataFrame({
        "Nom": [f"Soignant {i+1}" for i in range(18)],
        "Contrat (%)": [100]*15 + [80]*3
    })
    # Éditeur de tableau sur le site web
    df_equipe = st.data_editor(data_base, num_rows="dynamic", use_container_width=True)

# --- 3. LE MOTEUR IA & GÉNÉRATION EXCEL ---
if st.button("🚀 GÉNÉRER LE PLANNING PARFAIT", type="primary", use_container_width=True):
    with st.spinner("L'IA calcule des millions de combinaisons pour trouver le planning parfait..."):
        
        jours = nb_semaines * 7
        noms_salaries = df_equipe["Nom"].tolist()
        contrats = df_equipe["Contrat (%)"].tolist()
        total_salaries = len(noms_salaries)
        
        # Jours max à travailler
        max_jours = [int((c / 100) * 5 * nb_semaines) for c in contrats]
        shifts = ['M', 'A', 'C']
        
        model = cp_model.CpModel()
        x = {}
        for e in range(total_salaries):
            for d in range(jours):
                for s in shifts:
                    x[(e, d, s)] = model.NewBoolVar(f'shift_{e}_{d}_{s}')
                    
        # RÈGLES (Contraintes)
        for e in range(total_salaries):
            # 1 poste max par jour
            for d in range(jours):
                model.AddAtMostOne(x[(e, d, s)] for s in shifts)
            # Respect des jours du contrat
            model.Add(sum(x[(e, d, s)] for d in range(jours) for s in shifts) == max_jours[e])
            # Pas de A -> M
            for d in range(jours - 1):
                model.AddImplication(x[(e, d, 'A')], x[(e, d+1, 'M')].Not())
            # WE en bloc (Samedi = Dimanche)
            for w in range(nb_semaines):
                sat, sun = w * 7 + 5, w * 7 + 6
                for s in shifts:
                    model.Add(x[(e, sat, s)] == x[(e, sun, s)])

        # Quotas journaliers de l'EHPAD
        for d in range(jours):
            is_we = (d % 7 >= 5)
            if is_we:
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) == 6)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 3)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 2)
            else:
                model.Add(sum(x[(e, d, 'M')] for e in range(total_salaries)) == 8)
                model.Add(sum(x[(e, d, 'A')] for e in range(total_salaries)) == 4)
                model.Add(sum(x[(e, d, 'C')] for e in range(total_salaries)) == 1)

        # Lancement du calcul
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 60.0
        status = solver.Solve(model)

        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            st.success("✅ Solution mathématique parfaite trouvée !")
            
            # --- CRÉATION DE L'EXCEL PRO ---
            planning_data = []
            audit_data = []
            
            for e in range(total_salaries):
                ligne = []
                j_travailles = 0
                we_travailles = 0
                for d in range(jours):
                    poste = "Repos"
                    for s in shifts:
                        if solver.Value(x[(e, d, s)]) == 1:
                            poste = s
                            j_travailles += 1
                            if d % 7 == 6: # Si on travaille le dimanche, on compte 1 WE
                                we_travailles += 1
                    ligne.append(poste)
                planning_data.append(ligne)
                audit_data.append(f"✅ {j_travailles}/{max_jours[e]} jrs | {we_travailles} WE | 0 A->M")

            # Dates pour les colonnes
            colonnes = [(date_debut + timedelta(days=i)).strftime('%a %d/%m') for i in range(jours)]
            df = pd.DataFrame(planning_data, columns=colonnes, index=noms_salaries)
            df['AUDIT RÈGLES'] = audit_data
            
            # Stylisation du fichier Excel en mémoire
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Planning')
                workbook = writer.book
                worksheet = writer.sheets['Planning']
                
                # Formats
                format_M = workbook.add_format({'bg_color': '#D4EFDF', 'font_color': '#145A32', 'align': 'center'}) # Vert
                format_A = workbook.add_format({'bg_color': '#FCF3CF', 'font_color': '#9A7D0A', 'align': 'center'}) # Jaune
                format_C = workbook.add_format({'bg_color': '#FADBD8', 'font_color': '#78281F', 'align': 'center'}) # Rouge
                format_R = workbook.add_format({'font_color': '#BFC9CA', 'align': 'center'}) # Gris
                format_WE = workbook.add_format({'bg_color': '#EBEDEF', 'align': 'center'}) # Fond gris WE
                format_Audit = workbook.add_format({'font_color': '#1E8449', 'bold': True})
                
                worksheet.set_column('A:A', 25) # Largeur colonne noms
                worksheet.set_column(1, jours, 10) # Largeur jours
                worksheet.set_column(jours + 1, jours + 1, 35) # Largeur Audit
                
                # Appliquer les couleurs cellule par cellule
                for row_num in range(total_salaries):
                    for col_num in range(jours):
                        val = df.iloc[row_num, col_num]
                        cell_format = format_M if val == 'M' else format_A if val == 'A' else format_C if val == 'C' else format_R
                        if val == 'Repos' and (col_num % 7 >= 5): cell_format = format_WE
                        worksheet.write(row_num + 1, col_num + 1, val, cell_format)
                    # Écrire la colonne d'audit
                    worksheet.write(row_num + 1, jours + 1, df.iloc[row_num, jours], format_Audit)

            st.download_button(
                label="📥 TÉLÉCHARGER LE FICHIER EXCEL PRO",
                data=output.getvalue(),
                file_name=f"Planning_EHPAD_{date_debut.strftime('%d-%m-%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.error("❌ Impossible de trouver un planning avec ces données. Il n'y a pas assez de soignants ou les contraintes sont mathématiquement impossibles.")
